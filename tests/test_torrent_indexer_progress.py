"""Indexer diagnostics remain bounded, visible and private while searches wait."""
import contextlib
import copy
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "cli" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load("ott_indexer_progress_tests", "ott.py")
indexer = load("indexer_progress_tests", "torrent_indexer.py")
RUNTIME = "diagnostic-runtime"
PRIVATE_QUERY = "private diagnostic query"
PRIVATE_ENDPOINT = "http://127.0.0.1:9696/private-api"
PRIVATE_KEY = "private-fixture-api-key"
REFERENCE = "a" * 32


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Client:
    def __init__(self):
        self.clock = Clock()
        self.config = {"players": {"t1": "dev1", "t2": "dev2"}}
        self.server, self.timeout, self.on_receipt = "https://controller.invalid", 45, None
        self.calls, self.before = [], None
        self.search_available, self.max_limit = True, 100
        self.children = []

    def for_group(self, cancelled):
        child = Client()
        child.before = self.before
        child.group_cancelled = cancelled
        self.children.append(child)
        return child

    def call(self, device, action, request):
        self.calls.append((action, copy.deepcopy(request), self.timeout))
        if self.before is not None:
            self.before(self, device, action, request)
        if action == "capabilities":
            return {"version": 1, "player": {"version": "test", "platform": "tauri", "runtime": RUNTIME},
                    "lifecycle": [], "input": [], "playback": [], "torrent_indexer": {"version": 1}}
        if action != "torrent_indexer" or request["op"] not in ("caps", "search"):
            raise AssertionError("diagnostics must not configure, prepare or start playback")
        result = {"version": 1, "runtime": RUNTIME, "op": request["op"], "schema": 1,
                  "sourceId": request["sourceId"]}
        if request["op"] == "caps":
            return {**result, "searchAvailable": self.search_available,
                    "categories": [], "maxLimit": self.max_limit}
        return {**result, "items": [{"resultRef": REFERENCE, "title": PRIVATE_QUERY, "size": None,
                "seeders": None, "publishedAt": None, "categoryIds": [], "languages": []}],
                "nextOffset": None, "total": None}


class IndexerDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.stages = []

    def factory(self):
        session = indexer.Session
        return mock.patch.object(indexer, "Session", side_effect=lambda api, client, *args, **kwargs:
                                 session(api, client, *args, clock=client.clock,
                                         sleep=client.clock.sleep, **kwargs))

    def execute(self, words, timeout=45, progress=None):
        output, errors = io.StringIO(), io.StringIO()
        with self.factory(), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = indexer.run(ott, self.client, "dev1", words, timeout, True,
                               progress=self.stages.append if progress is None else progress)
        raw = output.getvalue()
        for private in (PRIVATE_ENDPOINT, PRIVATE_KEY):
            self.assertNotIn(private, raw + errors.getvalue() + repr(self.stages))
        self.assertEqual(self.client.timeout, 45)
        return code, json.loads(raw)

    def operations(self):
        return [request["op"] for action, request, _ in self.client.calls if action == "torrent_indexer"]

    def test_doctor_and_diagnose_only_discover_and_read_caps(self):
        for command in ("doctor", "diagnose"):
            with self.subTest(command=command):
                self.client, self.stages = Client(), []
                self.client.search_available = False
                code, result = self.execute([command, "home"])
                self.assertEqual((code, result["status"], result["op"]), (0, "ok", "doctor"))
                self.assertFalse(result["search_tested"])
                self.assertFalse(result["searchAvailable"])
                self.assertNotIn("result_count", result)
                self.assertEqual(self.operations(), ["caps"])
                self.assertEqual(self.stages, ["Checking player capabilities", "Checking source capabilities"])
                self.assertEqual([row["stage"] for row in result["checks"]], ["capabilities", "source_caps"])

    def test_query_is_one_small_search_and_result_metadata_stays_private(self):
        for advertised_limit in (100, 3):
            with self.subTest(advertised_limit=advertised_limit):
                self.client, self.stages = Client(), []
                self.client.max_limit = advertised_limit
                code, result = self.execute(["doctor", "home", "--query", PRIVATE_QUERY])
                self.assertEqual((code, result["search_tested"], result["result_count"]), (0, True, 1))
                self.assertEqual(self.operations(), ["caps", "search"])
                query = self.client.calls[-1][1]
                self.assertEqual(query, {"version": 1, "runtime": RUNTIME, "op": "search", "sourceId": "home",
                                         "query": PRIVATE_QUERY, "categoryIds": [], "offset": 0,
                                         "limit": min(5, advertised_limit)})
                self.assertNotIn(PRIVATE_QUERY, json.dumps(result) + repr(self.stages))
                self.assertNotIn(REFERENCE, json.dumps(result))
                self.assertEqual(self.stages[-1], "Searching the configured indexer source")

    def test_stage_timings_include_pacing_and_share_one_deadline(self):
        def consume(client, device, action, request):
            client.clock.value += .25 if action == "capabilities" else .5 if request["op"] == "caps" else .75

        self.client.before = consume
        code, result = self.execute(["doctor", "home", "--query", PRIVATE_QUERY], timeout=10)
        self.assertEqual(code, 0)
        self.assertEqual([timeout for _, _, timeout in self.client.calls], [10, 8.25, 6.75])
        self.assertEqual(result["elapsed_seconds"], 4)
        self.assertEqual(result["checks"], [
            {"stage": "capabilities", "status": "ok", "elapsed_seconds": .25},
            {"stage": "source_caps", "status": "ok", "elapsed_seconds": 2},
            {"stage": "search", "status": "ok", "elapsed_seconds": 1.75}])

    def test_expired_caps_budget_prevents_search_and_rejects_late_success(self):
        def consume(client, device, action, request):
            client.clock.value += .25 if action == "capabilities" else 2.25

        self.client.before = consume
        code, result = self.execute(["doctor", "home", "--query", PRIVATE_QUERY], timeout=4)
        self.assertEqual((code, result["reason"], result["stage"]), (1, "deadline_exceeded", "source_caps"))
        self.assertEqual(self.operations(), ["caps"])
        self.assertFalse(result["search_tested"])
        self.assertEqual(result["checks"][-1]["status"], "failed")
        self.assertIn("total CLI time budget", result["hint"])
        self.assertNotIn("result_count", result)

    def test_search_timeout_has_specific_guidance_without_replaying_or_echoing_upstream_text(self):
        for reason, hint in (("torznab_timeout", "Increasing the CLI timeout does not extend"),
                             ("companion_timeout", "Check the companion")):
            with self.subTest(reason=reason):
                self.client, self.stages = Client(), []

                def reject(client, device, action, request):
                    if request.get("op") == "search":
                        raise ott.PlayerRejected(PRIVATE_KEY + PRIVATE_ENDPOINT + PRIVATE_QUERY,
                            {"version": 1, "runtime": RUNTIME, "op": "search",
                             "error": "Torrent indexer request failed.", "reason": reason})

                self.client.before = reject
                code, result = self.execute(["doctor", "home", "--query", PRIVATE_QUERY])
                self.assertEqual((code, result["reason"], result["stage"]), (1, reason, "search"))
                self.assertIn(hint, result["hint"])
                self.assertTrue(result["search_tested"])
                self.assertFalse(result["replay_allowed"])
                self.assertNotIn(PRIVATE_QUERY, json.dumps(result) + repr(self.stages))
                self.assertEqual(self.operations(), ["caps", "search"])

    def test_safe_receipt_survives_transport_failure_without_private_exception_text(self):
        def fail(client, device, action, request):
            if request.get("op") == "search":
                failure = ott.RequestError("request_pending_timeout", "receipt", PRIVATE_KEY + PRIVATE_ENDPOINT,
                                           request_id="b" * 32)
                failure.action = "torrent_indexer"
                raise failure

        self.client.before = fail
        code, result = self.execute(["doctor", "home", "--query", PRIVATE_QUERY])
        self.assertEqual((code, result["reason"], result["stage"]), (1, "request_pending_timeout", "search"))
        self.assertEqual(result["request"], {"action": "torrent_indexer", "request_id": "b" * 32,
                                             "stage": "receipt", "reason": "request_pending_timeout"})
        self.assertIn("does not establish that the player is offline", result["hint"])
        self.assertEqual(self.operations(), ["caps", "search"])

    def test_group_cancellation_during_pacing_prevents_the_next_rpc(self):
        event, gaps = threading.Event(), []

        def wait(seconds):
            gaps.append(seconds)
            event.set()
            return True

        self.client.group_cancelled = SimpleNamespace(is_set=event.is_set, wait=wait)
        with mock.patch.object(self.client.clock, "sleep", side_effect=AssertionError("uncancellable sleep")):
            code, result = self.execute(["doctor", "home", "--query", PRIVATE_QUERY])
        self.assertEqual(code, 130)
        self.assertFalse(result["replay_allowed"])
        self.assertEqual(gaps, [1.5])
        self.assertEqual([action for action, _, _ in self.client.calls], ["capabilities"])

    def test_failed_routed_search_reports_stopped_and_retires_its_heartbeat(self):
        def reject(client, device, action, request):
            if request.get("op") == "search":
                raise ott.PlayerRejected(PRIVATE_KEY,
                    {"version": 1, "runtime": RUNTIME, "op": "search",
                     "error": "Torrent indexer request failed.", "reason": "torznab_timeout"})

        self.client.before = reject
        args = SimpleNamespace(words=["t1", "torrent", "indexer", "doctor", "home", "--query", PRIVATE_QUERY],
                               json=True, timeout=45, config="/unused/config.json")
        output, errors = io.StringIO(), io.StringIO()
        progress = ott.IndexerProgress("t1", errors)
        with self.factory(), mock.patch.object(ott, "torrent_indexer_module", return_value=indexer), \
                mock.patch.object(ott, "IndexerProgress", return_value=progress), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.execute_player_command(self.client, self.client.config, args, [], "dev1")
        self.assertEqual((code, json.loads(output.getvalue())["reason"]), (1, "torznab_timeout"))
        self.assertEqual(errors.getvalue().count("Indexer request stopped"), 1)
        self.assertNotIn("Indexer request complete", errors.getvalue())
        self.assertTrue(progress.stopped.is_set())
        self.assertFalse(progress.thread.is_alive())
        for private in (PRIVATE_QUERY, PRIVATE_KEY, PRIVATE_ENDPOINT):
            self.assertNotIn(private, errors.getvalue() + output.getvalue())

    def test_closed_progress_output_does_not_change_diagnostics(self):
        def closed(stage):
            raise OSError(PRIVATE_KEY)

        code, result = self.execute(["doctor", "home"], progress=closed)
        self.assertEqual((code, result["status"]), (0, "ok"))
        self.assertEqual(self.operations(), ["caps"])

    def test_legacy_search_retains_its_result_handle_and_exact_json_shape(self):
        code, result = self.execute(["search", "home", "ordinary query"])
        self.assertEqual(code, 0)
        self.assertEqual(set(result), {"status", "version", "runtime", "op", "schema", "sourceId",
                                       "items", "nextOffset", "total"})
        self.assertEqual(result["items"][0]["resultRef"], REFERENCE)
        self.assertEqual(self.operations(), ["caps", "search"])

    def test_group_search_heartbeats_are_live_while_json_stdout_waits_for_results(self):
        blocked, release, heartbeats = threading.Event(), threading.Event(), threading.Event()
        lock = threading.Lock()
        reached, progress_instances, outcome = set(), [], []

        class Diagnostics(io.StringIO):
            def write(self, text):
                with lock:
                    size = super().write(text)
                    value = self.getvalue()
                    if all("[indexer " + player + "] Still working: Searching the configured indexer source" in value
                           for player in ("t1", "t2")):
                        heartbeats.set()
                    return size

        class Progress(ott.IndexerProgress):
            heartbeat_seconds = .01

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                progress_instances.append(self)

        def hold(client, device, action, request):
            if request.get("op") == "search":
                with lock:
                    reached.add(device)
                    if len(reached) == 2:
                        blocked.set()
                if not release.wait(5):
                    raise AssertionError("test did not release the held search")

        self.client.before = hold
        args = SimpleNamespace(words=["t1,t2", "torrent", "indexer", "doctor", "home", "--query", PRIVATE_QUERY],
                               json=True, timeout=45, config="/unused/config.json")
        output, errors = io.StringIO(), Diagnostics()

        def run():
            try:
                outcome.append(ott.player_group_command(self.client, self.client.config,
                                                        [("t1", "dev1"), ("t2", "dev2")], args, []))
            except BaseException as exc:
                outcome.append(exc)

        with self.factory(), mock.patch.object(ott, "IndexerProgress", Progress), \
                mock.patch.object(ott, "torrent_indexer_module", return_value=indexer), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                self.assertTrue(blocked.wait(5), "both player searches must be active")
                self.assertTrue(heartbeats.wait(5), "each held search must report a live heartbeat")
                self.assertEqual(output.getvalue(), "", "progress must not contaminate aggregate JSON")
            finally:
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, [0])
        result = json.loads(output.getvalue())
        self.assertEqual(result["succeeded"], 2)
        self.assertEqual([row["player"] for row in result["results"]], ["t1", "t2"])
        for row in result["results"]:
            self.assertEqual(row["data"]["result_count"], 1)
            self.assertNotIn("warnings", row, "live progress belongs to stderr, not retained per-player warnings")
        for player in ("t1", "t2"):
            self.assertEqual(errors.getvalue().count("[indexer " + player + "] Indexer request complete"), 1)
        self.assertEqual(len(progress_instances), 2)
        for progress in progress_instances:
            self.assertTrue(progress.stopped.is_set())
            self.assertFalse(progress.thread.is_alive())
        for private in (PRIVATE_QUERY, PRIVATE_ENDPOINT, PRIVATE_KEY):
            self.assertNotIn(private, output.getvalue() + errors.getvalue())
        for client in self.client.children:
            self.assertEqual([params["op"] for action, params, _ in client.calls if action == "torrent_indexer"],
                             ["caps", "search"])


if __name__ == "__main__":
    unittest.main()
