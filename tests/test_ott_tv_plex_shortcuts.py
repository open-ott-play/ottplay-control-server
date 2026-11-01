import contextlib
import copy
import importlib.util
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tv_plex_shortcut", ROOT / "scripts/ott-tv-plex.py")
shortcut = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shortcut)
_, ott = shortcut.load_cli(ROOT / "cli/ott.py")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def television():
    return {"version": 1, "device_id": "dev_lg2", "action": "go", "transport": "webos",
            "outcome": "accepted", "state": {"power": "on"}}


def caps():
    return {"version": 1, "player": {"version": "1.1.54", "platform": "webos", "runtime": "page-lg2"},
            "lifecycle": [], "input": [], "playback": [],
            "plex_queue": {"version": 1, "operations": ["status", "play"], "max_items": 500},
            "plex_library": {"version": 1, "operations": ["list", "play"], "max_items": 500}}


def queue(state="preparing"):
    return {"version": 1, "runtime": "page-lg2", "active": True, "state": state,
            "ids": ["11", "12"], "index": 0, "repeat": "none", "order": "listed"}


def library(operation="list"):
    row = {"id": "7", "title": shortcut.COLLECTION, "type": "show"}
    result = {"version": 1, "runtime": "page-lg2", "op": operation, "state": "ready"}
    if operation == "list":
        result["libraries"] = [row]
    else:
        result.update(library=row, shuffled=True, queue=queue())
    return result


def native_config():
    return {"ha_url": "http://127.0.0.1:8123", "ha_token_file": "/tmp/test-ha-token",
            "plex_entity": "media_player.native_plex", "scan_entity": "button.scan_plex_clients",
            "expected_app_id": "native-plex"}


def native_state(state="idle", updated=0, library=None, position=None):
    return {"entity_id": "media_player.native_plex", "state": state,
            "last_updated": f"2026-11-01T12:00:{updated:02d}+00:00",
            "attributes": {"supported_features": 512, "media_library_title": library,
                           "media_content_id": "123" if library else None,
                           "media_position": position,
                           "media_position_updated_at": f"2026-11-01T12:00:{updated:02d}+00:00"}}


class FakeHA:
    def __init__(self, states, failures=()):
        self.states, self.failures, self.calls = list(states), list(failures), []

    def state(self, entity, seconds=6):
        self.calls.append(("state", entity))
        result = self.states.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    def service(self, domain, service, body, seconds=6):
        self.calls.append((service, copy.deepcopy(body)))
        if self.failures:
            result = self.failures.pop(0)
            if isinstance(result, Exception):
                raise result
        return []


class FakeRunner:
    def __init__(self, responses, clock):
        self.responses = list(responses)
        self.calls = []
        self.clock = clock

    def call(self, words, seconds, mutating=False):
        self.calls.append((list(words), seconds, mutating))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response()
        return copy.deepcopy(response)


class WorkflowTests(unittest.TestCase):
    def run_workflow(self, responses):
        clock, stream = Clock(), io.StringIO()
        runner = FakeRunner(responses, clock)
        progress = shortcut.Progress(stream, clock)
        workflow = shortcut.Workflow(ott, runner, progress, 240, clock, clock.sleep)
        return workflow, runner, stream, clock

    def test_waits_for_live_controls_and_exact_queue_without_replaying_mutations(self):
        workflow, runner, stream, _ = self.run_workflow([
            television(), shortcut.ReadPending(), caps(), library(), library("play"),
            shortcut.ReadPending(), queue(), queue("playing")])
        self.assertEqual(workflow.run()["state"], "playing")
        mutations = [call[0] for call in runner.calls if call[2]]
        self.assertEqual(mutations, [["tv", "go"], ["plex", "shuffle", "MultCocomelonAll"]])
        self.assertIn("Waiting for OttPlay", stream.getvalue())
        self.assertIn("Playback confirmed", stream.getvalue())
        self.assertNotIn("state", [call[0][0] for call in runner.calls])

    def test_missing_capability_is_polled_before_any_shuffle(self):
        missing = caps()
        del missing["plex_queue"]
        workflow, runner, _, _ = self.run_workflow([
            television(), missing, caps(), library(), library("play"), queue("playing")])
        workflow.run()
        self.assertEqual(sum(call[0] == ["caps"] for call in runner.calls), 2)

    def test_runtime_change_during_readiness_is_safe_to_poll(self):
        other = library()
        other["runtime"] = "page-old"
        workflow, runner, _, _ = self.run_workflow([
            television(), caps(), other, caps(), library(), library("play"), queue("playing")])
        workflow.run()
        self.assertEqual(sum(call[0] == ["plex", "shuffle", shortcut.COLLECTION] for call in runner.calls), 1)

    def test_ambiguous_mutation_is_not_repeated(self):
        for responses in ([shortcut.ShortcutError("uncertain")],
                          [television(), caps(), library(), shortcut.ShortcutError("uncertain")]):
            with self.subTest(stage=len(responses)):
                workflow, runner, _, _ = self.run_workflow(responses)
                with self.assertRaises(shortcut.ShortcutError):
                    workflow.run()
                self.assertEqual(len(runner.calls), len(responses))

    def test_different_queue_or_runtime_never_counts_as_confirmation(self):
        for field, value in (("ids", ["31", "32"]), ("runtime", "page-new")):
            changed = queue("playing")
            changed[field] = value
            workflow, runner, _, _ = self.run_workflow([
                television(), caps(), library(), library("play"), changed])
            with self.subTest(field=field), self.assertRaisesRegex(shortcut.ShortcutError, "changed"):
                workflow.run()
            self.assertEqual(len([call for call in runner.calls if call[2]]), 2)

    def test_error_queue_stops_and_paused_queue_does_not_claim_playback(self):
        error = queue("error")
        workflow, _, _, _ = self.run_workflow([
            television(), caps(), library(), library("play"), error])
        with self.assertRaisesRegex(shortcut.ShortcutError, "stopped"):
            workflow.run()
        workflow, runner, stream, clock = self.run_workflow([
            television(), caps(), library(), library("play"), *[queue("paused") for _ in range(50)]])
        with self.assertRaisesRegex(shortcut.ShortcutError, "Timed out"):
            workflow.run()
        self.assertLessEqual(clock(), 45)
        self.assertNotIn("Playback confirmed", stream.getvalue())
        self.assertEqual(len([call for call in runner.calls if call[2]]), 2)

    def test_readiness_timeout_does_not_dispatch_shuffle(self):
        workflow, runner, _, clock = self.run_workflow([television(), *[shortcut.ReadPending() for _ in range(60)]])
        with self.assertRaises(shortcut.ShortcutError):
            workflow.run()
        self.assertLessEqual(clock(), 60)
        self.assertEqual([call[0] for call in runner.calls if call[2]], [["tv", "go"]])

    def test_truncated_queue_preserves_all_returned_ids(self):
        supported = caps()
        supported["plex_library_truncation"] = {"version": 1}
        truncated = library("play")
        truncated["truncated"] = True
        truncated["queue"].update(ids=[str(i + 1) for i in range(500)], state="playing")
        workflow, _, stream, _ = self.run_workflow([television(), supported, library(), truncated])
        self.assertEqual(len(workflow.run()["ids"]), 500)
        self.assertIn("queue limit of 500", stream.getvalue())

    def test_invalid_metadata_is_sanitized(self):
        bad = caps()
        bad["player"]["runtime"] = "https://private.example/token=secret-value"
        workflow, _, _, _ = self.run_workflow([television(), bad])
        with self.assertRaises(shortcut.ShortcutError) as raised:
            workflow.run()
        self.assertNotIn("secret-value", str(raised.exception))

    def test_progress_heartbeats_are_on_stderr_and_every_five_seconds(self):
        clock, stream = Clock(), io.StringIO()
        progress = shortcut.Progress(stream, clock)
        progress.phase("Waiting")
        clock.sleep(4.9)
        progress.tick()
        self.assertEqual(len(stream.getvalue().splitlines()), 1)
        clock.sleep(0.1)
        progress.tick()
        self.assertEqual(len(stream.getvalue().splitlines()), 2)


class NativeWorkflowTests(unittest.TestCase):
    def workflow(self, states, failures=()):
        clock, stream = Clock(), io.StringIO()
        opened = television()
        opened.update(action="status", state={"power": "on", "app_id": "native-plex"})
        runner = FakeRunner([television(), opened], clock)
        ha = FakeHA(states, failures)
        progress = shortcut.Progress(stream, clock, "cocop")
        flow = shortcut.NativeWorkflow(ott, runner, progress, 240, native_config(), ha, clock, clock.sleep)
        return flow, ha, runner, stream, clock

    def test_launches_native_app_and_sends_exactly_one_ha_play_request(self):
        flow, ha, runner, stream, _ = self.workflow([
            native_state("unavailable"), native_state(),
            native_state("playing", 1, "Other library"), native_state("playing", 2, shortcut.COLLECTION)])
        self.assertEqual(flow.run()["library"], shortcut.COLLECTION)
        self.assertEqual(runner.calls[0][0], ["tv", "go", "plex"])
        services = [call for call in ha.calls if call[0] != "state"]
        self.assertEqual([call[0] for call in services], ["press", "play_media"])
        payload = services[1][1]
        self.assertEqual(payload["entity_id"], "media_player.native_plex")
        self.assertEqual(payload["media_content_type"], "movie")
        self.assertEqual(json.loads(payload["media_content_id"]), {
            "library_name": shortcut.COLLECTION, "libtype": "movie", "allow_multiple": True,
            "maxresults": 500, "shuffle": True})
        self.assertIn("[cocop lg2]", stream.getvalue())

    def test_same_cached_playing_state_does_not_confirm_new_play(self):
        stale = native_state("playing", 0, shortcut.COLLECTION)
        flow, ha, _, stream, clock = self.workflow([stale] * 50)
        with self.assertRaisesRegex(shortcut.ShortcutError, "Timed out"):
            flow.run()
        self.assertEqual(sum(call[0] == "play_media" for call in ha.calls), 1)
        self.assertLessEqual(clock(), 45)
        self.assertNotIn("playback confirmed", stream.getvalue())

    def test_old_playback_with_advancing_timestamps_and_position_does_not_confirm(self):
        states = [native_state("playing", second, shortcut.COLLECTION, 10 + second) for second in range(50)]
        flow, ha, _, stream, _ = self.workflow(states)
        with self.assertRaisesRegex(shortcut.ShortcutError, "Timed out"):
            flow.run()
        self.assertEqual(sum(call[0] == "play_media" for call in ha.calls), 1)
        self.assertNotIn("playback confirmed", stream.getvalue())

    def test_changed_media_id_or_same_item_position_reset_confirms(self):
        for changed_item in (True, False):
            before = native_state("playing", 0, shortcut.COLLECTION, 10)
            after = native_state("playing", 1, shortcut.COLLECTION, 0)
            if changed_item:
                after["attributes"].update(media_content_id="456", media_position=11)
            flow, ha, _, _, _ = self.workflow([before, after])
            with self.subTest(changed_item=changed_item):
                self.assertEqual(flow.run()["state"], "playing")
                self.assertEqual(sum(call[0] == "play_media" for call in ha.calls), 1)

    def test_off_and_standby_are_not_ready_despite_play_capability(self):
        flow, ha, _, _, _ = self.workflow([
            native_state("off"), native_state("standby"), native_state(),
            native_state("playing", 1, shortcut.COLLECTION)])
        flow.run()
        play_index = next(i for i, call in enumerate(ha.calls) if call[0] == "play_media")
        self.assertEqual(sum(call[0] == "state" for call in ha.calls[:play_index]), 3)

    def test_delayed_client_is_discovered_by_bounded_rescan_even_after_scan_timeout(self):
        for first_scan in (None, shortcut.ReadPending()):
            states = [native_state("unavailable")] * 10
            states += [native_state(), native_state("playing", 1, shortcut.COLLECTION)]
            flow, ha, _, _, clock = self.workflow(states, [first_scan])
            with self.subTest(first_scan=type(first_scan).__name__):
                flow.run()
                self.assertEqual(sum(call[0] == "press" for call in ha.calls), 2)
                self.assertEqual(sum(call[0] == "play_media" for call in ha.calls), 1)
                self.assertGreaterEqual(clock(), 10)
                self.assertLess(clock(), 60)

    def test_only_discovery_button_uses_retryable_http_classification(self):
        runner = mock.Mock()
        ha = shortcut.HomeAssistant(native_config(), runner)
        ha.service("button", "press", {"entity_id": native_config()["scan_entity"]})
        self.assertFalse(runner.ha_call.call_args.kwargs["mutating"])
        ha.service("media_player", "play_media", {})
        self.assertTrue(runner.ha_call.call_args.kwargs["mutating"])

    def test_uncertain_ha_play_is_not_repeated(self):
        flow, ha, _, _, _ = self.workflow([native_state()], [None, shortcut.ShortcutError("uncertain")])
        with self.assertRaisesRegex(shortcut.ShortcutError, "uncertain"):
            flow.run()
        self.assertEqual(sum(call[0] == "play_media" for call in ha.calls), 1)

    def test_missing_play_capability_never_sends_play(self):
        state = native_state()
        state["attributes"]["supported_features"] = 0
        flow, ha, _, _, clock = self.workflow([state] * 65)
        with self.assertRaises(shortcut.ShortcutError):
            flow.run()
        self.assertFalse(any(call[0] == "play_media" for call in ha.calls))
        self.assertLessEqual(clock(), 60)

    def test_foreground_app_is_checked_before_discovery_or_play(self):
        flow, ha, runner, _, _ = self.workflow([native_state(), native_state("playing", 1, shortcut.COLLECTION)])
        correct = runner.responses[-1]
        wrong = copy.deepcopy(correct)
        wrong["state"]["app_id"] = "msx"
        runner.responses[1:] = [wrong, shortcut.ReadPending(), correct]
        flow.run()
        self.assertEqual([call[0] for call in runner.calls],
                         [["tv", "go", "plex"], ["tv", "status"], ["tv", "status"], ["tv", "status"]])

    def test_invalid_config_or_state_is_rejected_without_private_details(self):
        for url in ("http://user:secret@example.test", "http://example.test/?token=secret",
                    "file:///tmp/config", "https://example.test/#secret", "http://["):
            with self.subTest(url=url), self.assertRaises(shortcut.ShortcutError) as error:
                shortcut.native_config(dict(native_config(), ha_url=url))
            self.assertNotIn("secret", str(error.exception))
        for change in ({"supported_features": True}, {"media_position_updated_at": "invalid"},
                       {"media_content_id": {"token": "secret"}}, {"media_position": True},
                       {"media_position": float("nan")}, {"media_position": -1}, {"media_position": 10 ** 1000}):
            raw = native_state("playing", 1, shortcut.COLLECTION)
            raw["attributes"].update(change)
            with self.subTest(change=change), self.assertRaises(shortcut.ShortcutError):
                shortcut.native_state(raw, "media_player.native_plex")


@unittest.skipIf(os.name == "nt", "The Bash shortcut process and lock boundary requires POSIX")
class HostBoundaryTests(unittest.TestCase):
    def test_lock_rejects_overlap_and_releases_after_failure_without_unlinking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lg2.lock"
            with shortcut.television_lock(path):
                inode = path.stat().st_ino
                with self.assertRaisesRegex(shortcut.ShortcutError, "Another LG2"):
                    with shortcut.television_lock(path):
                        self.fail("overlap accepted")
            with self.assertRaisesRegex(RuntimeError, "failure"):
                with shortcut.television_lock(path):
                    raise RuntimeError("failure")
            with shortcut.television_lock(path):
                self.assertEqual(path.stat().st_ino, inode)

    def test_lock_rejects_symlink_or_non_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lg2.lock"
            target = Path(directory) / "target"
            target.touch()
            path.symlink_to(target)
            with self.assertRaises(OSError):
                with shortcut.television_lock(path):
                    pass
            path.unlink()
            path.touch(mode=0o644)
            path.chmod(0o644)
            with self.assertRaises(shortcut.ShortcutError):
                with shortcut.television_lock(path):
                    pass

    def test_installed_symlink_is_resolved_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ott"
            path.symlink_to(ROOT / "cli/ott.py")
            resolved, api = shortcut.load_cli(path)
            self.assertEqual(resolved, ROOT / "cli/ott.py")
            self.assertTrue(callable(api.plex_queue_metadata))

    def run_script(self, content, mutating=False):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake.py"
            script.write_text(content)
            progress = shortcut.Progress(io.StringIO())
            runner = shortcut.Runner(script, progress, time.monotonic() + 3)
            return runner.call(["caps"], 1, mutating=mutating)

    def test_runner_uses_json_no_shell_and_never_prints_failed_output(self):
        result = self.run_script("import json,sys; print(json.dumps({'args':sys.argv[1:]}))")
        self.assertEqual(result["args"], ["--json", "-t", "1", "lg2", "caps"])
        with self.assertRaises(shortcut.ReadPending):
            self.run_script("import sys; print('token=private'); sys.exit(1)")
        with self.assertRaises(shortcut.ShortcutError) as raised:
            self.run_script("import sys; print('token=private'); sys.exit(1)", True)
        self.assertNotIn("private", str(raised.exception))

    def test_runner_kills_timed_out_child_and_never_repeats_mutation(self):
        original = subprocess.Popen
        children = []

        def record(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            return process

        with mock.patch.object(shortcut.subprocess, "Popen", side_effect=record):
            with self.assertRaisesRegex(shortcut.ShortcutError, "may have been accepted"):
                self.run_script("import time; time.sleep(30)", True)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_invalid_arguments_and_help_do_not_load_cli_or_take_lock(self):
        with mock.patch.object(shortcut, "television_lock") as lock, \
                mock.patch.object(shortcut, "load_cli") as load, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(shortcut.main(["--help"]), 0)
            self.assertEqual(shortcut.main(["extra"]), 2)
        lock.assert_not_called()
        load.assert_not_called()


@unittest.skipIf(os.name == "nt", "Private token files and Bash shortcuts require POSIX")
class NativeHTTPTests(unittest.TestCase):
    def test_native_http_uses_private_token_and_does_not_follow_redirects(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if self.server.redirect:
                    self.send_response(302)
                    self.send_header("Location", "/token-must-not-reach-here")
                    self.end_headers()
                else:
                    body = json.dumps(native_state()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.redirect = False
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                token = Path(directory) / "ha-token"
                token.write_text("test-token-never-for-production")
                token.chmod(0o600)
                config = dict(native_config(), ha_url=f"http://127.0.0.1:{server.server_port}", ha_token_file=str(token))
                runner = shortcut.Runner(ROOT / "cli/ott.py", shortcut.Progress(io.StringIO()), time.monotonic() + 15)
                ha = shortcut.HomeAssistant(config, runner)
                self.assertEqual(ha.state("media_player.native_plex")["state"], "idle")
                self.assertEqual(requests[-1][1], "Bearer test-token-never-for-production")
                server.redirect = True
                with self.assertRaises(shortcut.ReadPending):
                    ha.state("media_player.native_plex")
                self.assertEqual(len(requests), 2)
                token.chmod(0o644)
                with self.assertRaises(shortcut.ReadPending):
                    ha.state("media_player.native_plex")
                self.assertEqual(len(requests), 2)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
