"""Archive cancellation releases callers while private HTTP responses are held."""
import contextlib
import copy
import importlib.util
import io
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "programme_group_archive_cancel", Path(__file__).resolve().parents[1] / "cli" / "programme_search.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)

NOW = 1800000000
CHANNEL = {"id": "cats", "name": "Channel", "number": 1, "tvgId": "cats",
           "tvgName": "", "shift": 0, "archiveHours": 144}
SNAPSHOT = {"catalog": "receipt", "channels": [CHANNEL],
            "archive": {"version": 1, "revision": "source"}}
SETTINGS = {"url": "https://epg.invalid/epg/v1", "source": "epg-one"}


class HeldResponses:
    def __init__(self, expected=1):
        self.expected = expected
        self.started, self.release, self.closed = threading.Event(), threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.requests, self.reads, self.closes = [], 0, 0

    def open(self, request, **kwargs):
        with self.lock:
            self.requests.append(request.full_url)
        owner = self

        class Response(io.BytesIO):
            def read1(self, count):
                with owner.lock:
                    owner.reads += 1
                    if owner.reads == owner.expected:
                        owner.started.set()
                if not owner.release.wait(10):
                    raise AssertionError("held HTTP response was not released by test cleanup")
                return super().read1(count)

            def close(self):
                if not self.closed:
                    super().close()
                    with owner.lock:
                        owner.closes += 1
                        if owner.closes == owner.expected:
                            owner.closed.set()

        return Response(b"#EXTM3U\n#EXTINF:30,\nsegment.ts\n")


class GroupArchiveCancellationTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(search, "time", SimpleNamespace(
            time=lambda: NOW, monotonic=time.monotonic, sleep=time.sleep)))
        self.stack.enter_context(mock.patch.object(search, "https_handler", return_value=mock.Mock()))
        self.cache = self.stack.enter_context(mock.patch.object(search, "cached", return_value=None))
        self.saved = self.stack.enter_context(mock.patch.object(search, "remember"))
        self.hook = self.stack.enter_context(mock.patch.object(threading, "excepthook"))
        self.cancelled = threading.Event()
        self.client = mock.Mock(config={"server": "https://controller.invalid"}, timeout=45)
        self.client.call.side_effect = lambda device, action, params: (
            copy.deepcopy(SNAPSHOT) if action == "epg_catalog" else
            {"resolved": True, "url": "https://archive.invalid/private-stream"})

    def cancel_while_http_is_held(self, action, responses):
        outcomes, finished = [], threading.Event()
        callers_waiting = threading.Event()
        waiting_queues = set()

        class OutcomeQueue(search.queue.Queue):
            def get(self, *args, **kwargs):
                with responses.lock:
                    waiting_queues.add(id(self))
                    if len(waiting_queues) == responses.expected:
                        callers_waiting.set()
                return super().get(*args, **kwargs)

        def run():
            try:
                outcomes.append(action())
            except BaseException as exc:
                outcomes.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=run, daemon=True)
        queue_module = SimpleNamespace(Queue=OutcomeQueue, Empty=search.queue.Empty)
        with mock.patch.object(search, "build_opener", return_value=SimpleNamespace(open=responses.open)), \
                mock.patch.object(search, "queue", queue_module):
            worker.start()
            try:
                self.assertTrue(responses.started.wait(5), "archive HTTP request did not reach its body")
                self.assertTrue(callers_waiting.wait(5), "HTTP callers did not enter their result waits")
                self.cancelled.set()
                # The response remains blocked: completion must follow group
                # cancellation, not the socket returning or its 45s deadline.
                self.assertTrue(finished.wait(5), "cancelled caller is still waiting on private HTTP")
                self.assertFalse(responses.release.is_set())
                self.assertEqual(len(outcomes), 1)
                self.assertIsInstance(outcomes[0], KeyboardInterrupt)
            finally:
                responses.release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive(), "archive caller did not retire")
                self.assertTrue(responses.closed.wait(5), "cancelled HTTP response was not closed")
        self.hook.assert_not_called()
        self.assertEqual(responses.reads, responses.expected)

    def test_mapping_http_cancel_propagates_before_any_archive_resolution(self):
        responses = HeldResponses()
        self.cancel_while_http_is_held(lambda: search.search_archives(
            self.client, "tv", SETTINGS, SNAPSHOT, "Cats", deadline=time.monotonic() + 45,
            group_cancelled=self.cancelled), responses)
        self.assertEqual(responses.requests, [SETTINGS["url"] + "/match"])
        self.client.call.assert_not_called()
        self.saved.assert_not_called()

    def test_media_probe_cancel_never_resolves_the_next_candidate(self):
        responses = HeldResponses()
        prepared = {"current": [], "chains": [
            (CHANNEL, [{"time": NOW - 900, "time_to": NOW - 800, "name": "Cats"}]),
            (CHANNEL, [{"time": NOW - 700, "time_to": NOW - 600, "name": "Cats"}])], "until": NOW + 100}
        refresh = mock.Mock(return_value=[])
        self.cancel_while_http_is_held(lambda: search.search_archives(
            self.client, "tv", SETTINGS, SNAPSHOT, "Cats", prepared=prepared, refresh_current=refresh,
            fast=True, deadline=time.monotonic() + 45, group_cancelled=self.cancelled), responses)
        self.assertEqual(responses.requests, ["https://archive.invalid/private-stream"])
        self.assertEqual(self.client.call.call_count, 1)
        self.assertEqual(self.client.call.call_args.args[1], "resolve_archive")
        refresh.assert_not_called()
        self.saved.assert_not_called()

    def test_history_pool_stops_before_scheduling_another_batch_without_a_deadline(self):
        channels = [dict(CHANNEL, id=str(number), number=number + 1) for number in range(8)]
        mapping = {"generation": "g1", "until": NOW + 100,
                   "mappings": {channel["id"]: {"channelId": channel["id"], "shift": 0}
                                for channel in channels}}
        self.cache.side_effect = lambda kind, *args: mapping if kind == "mapping" else None
        responses = HeldResponses(expected=4)
        self.cancel_while_http_is_held(lambda: search.schedules(
            SETTINGS["url"], channels, group_cancelled=self.cancelled), responses)
        self.assertEqual(len(responses.requests), 4)
        self.assertTrue(all("/programmes?" in url for url in responses.requests))
        self.saved.assert_not_called()

    def test_cancelled_entry_points_do_not_start_transports(self):
        self.cancelled.set()
        action = mock.Mock()
        calls = [
            lambda: search._bounded_read(action, None, self.cancelled),
            lambda: search.epg_request(SETTINGS["url"], "/match", {}, group_cancelled=self.cancelled),
            lambda: search.archive_available("https://archive.invalid/stream", NOW,
                                              group_cancelled=self.cancelled),
            lambda: search.search_archives(self.client, "tv", SETTINGS, SNAPSHOT, "Cats",
                                            group_cancelled=self.cancelled),
        ]
        with mock.patch.object(search, "build_opener") as transport:
            for call in calls:
                with self.subTest(entry_point=call), self.assertRaises(KeyboardInterrupt):
                    call()
        action.assert_not_called()
        transport.assert_not_called()
        self.client.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
