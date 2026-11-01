import contextlib
import copy
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
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
    return {"plex_url": "http://127.0.0.1:32400", "plex_token_file": "/tmp/test-plex-token",
            "plex_client_id": "lg2-client", "plex_server_id": "test-server", "expected_app_id": "native-plex"}


def native_ready(ready=True):
    return {"ready": ready, "capabilities": ["playback", "playqueues"] if ready else []}


def native_queue():
    return {"queue_id": 3810, "count": 500, "truncated": True, "first_id": "123"}


def native_playing(queue_id=3810):
    return {"state": "playing", "queue_id": queue_id, "content_id": "123", "position": 1}


class FakeAdapter:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def call(self, operation, seconds=6, queue_id=None):
        self.calls.append((operation, seconds, queue_id))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


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
    def workflow(self, responses):
        clock, stream = Clock(), io.StringIO()
        opened = television()
        opened.update(action="status", state={"power": "on", "app_id": "native-plex"})
        runner = FakeRunner([television(), opened], clock)
        adapter = FakeAdapter(responses)
        progress = shortcut.Progress(stream, clock, "cocop")
        flow = shortcut.NativeWorkflow(ott, runner, progress, 240, native_config(), adapter, clock, clock.sleep)
        return flow, adapter, runner, stream, clock

    def test_prepares_and_plays_once_then_confirms_exact_queue_id(self):
        flow, adapter, runner, stream, _ = self.workflow([
            native_ready(False), shortcut.ReadPending(), native_ready(), native_queue(),
            {"accepted": True, "queue_id": 3810}, native_playing(3809), native_playing()])
        self.assertEqual(flow.run()["queue_id"], 3810)
        self.assertEqual(runner.calls[0][0], ["tv", "go", "plex"])
        operations = [call[0] for call in adapter.calls]
        self.assertEqual(operations.count("prepare"), 1)
        self.assertEqual(operations.count("play"), 1)
        self.assertEqual([call[2] for call in adapter.calls if call[0] in ("play", "status")], [3810, 3810, 3810])
        self.assertIn("[cocop lg2]", stream.getvalue())
        self.assertIn("limit of 500", stream.getvalue())
        self.assertIn("queue 3810 with 500 items (first item 123)", stream.getvalue())

    def test_old_playback_never_confirms_new_queue_even_while_position_advances(self):
        old = [dict(native_playing(3809), position=second) for second in range(50)]
        flow, adapter, _, stream, clock = self.workflow([
            native_ready(), native_queue(), {"accepted": True, "queue_id": 3810}, *old])
        with self.assertRaisesRegex(shortcut.ShortcutError, "Timed out"):
            flow.run()
        self.assertEqual(sum(call[0] == "play" for call in adapter.calls), 1)
        self.assertLessEqual(clock(), 45)
        self.assertNotIn("playback confirmed", stream.getvalue())

    def test_missing_queue_id_is_not_confirmation(self):
        unknown = {"state": "unknown", "queue_id": None, "content_id": None, "position": None}
        flow, _, _, _, _ = self.workflow([
            native_ready(), native_queue(), {"accepted": True, "queue_id": 3810}, unknown, native_playing()])
        self.assertEqual(flow.run()["queue_id"], 3810)

    def test_prepare_or_play_error_is_never_repeated(self):
        for fail_prepare in (True, False):
            responses = [native_ready()]
            if not fail_prepare:
                responses.append(native_queue())
            responses.append(shortcut.ShortcutError("uncertain"))
            flow, adapter, _, _, _ = self.workflow(responses)
            with self.subTest(fail_prepare=fail_prepare), self.assertRaisesRegex(shortcut.ShortcutError, "uncertain"):
                flow.run()
            self.assertEqual(sum(call[0] == "prepare" for call in adapter.calls), 1)
            self.assertEqual(sum(call[0] == "play" for call in adapter.calls), 0 if fail_prepare else 1)

    def test_foreground_app_is_checked_before_plex_discovery_or_play(self):
        flow, _, runner, _, _ = self.workflow([
            native_ready(), native_queue(), {"accepted": True, "queue_id": 3810}, native_playing()])
        correct = runner.responses[-1]
        wrong = copy.deepcopy(correct)
        wrong["state"]["app_id"] = "msx"
        runner.responses[1:] = [wrong, shortcut.ReadPending(), correct]
        flow.run()
        self.assertEqual([call[0] for call in runner.calls],
                         [["tv", "go", "plex"], ["tv", "status"], ["tv", "status"], ["tv", "status"]])

    def test_discovery_timeout_creates_no_queue_and_sends_no_play(self):
        flow, adapter, _, _, clock = self.workflow([native_ready(False)] * 65)
        with self.assertRaises(shortcut.ShortcutError):
            flow.run()
        self.assertTrue(all(call[0] == "discover" for call in adapter.calls))
        self.assertLessEqual(clock(), 60)

    def test_invalid_config_or_status_is_rejected_without_private_details(self):
        for url in ("http://user:secret@example.test", "http://example.test/?token=secret",
                    "file:///tmp/config", "https://example.test/#secret", "http://["):
            with self.subTest(url=url), self.assertRaises(shortcut.ShortcutError) as error:
                shortcut.native_config(dict(native_config(), plex_url=url))
            self.assertNotIn("secret", str(error.exception))
        for change in ({"queue_id": True}, {"position": float("nan")},
                       {"position": -1}, {"position": 10 ** 1000}, {"content_id": {"token": "secret"}}):
            flow, _, _, _, _ = self.workflow([
                native_ready(), native_queue(), {"accepted": True, "queue_id": 3810},
                dict(native_playing(), **change)])
            with self.subTest(change=change), self.assertRaises(shortcut.ShortcutError):
                flow.run()

    def test_adapter_pins_one_controller_and_increments_command_ids(self):
        runner = mock.Mock()
        adapter = shortcut.PlexAdapter(native_config(), runner)
        adapter.call("discover")
        adapter.call("prepare", 45)
        adapter.call("play", 45, 3810)
        adapter.call("status", 8, 3810)
        calls = runner.plex_call.call_args_list
        self.assertEqual([call.args[0]["command_id"] for call in calls], [1, 2, 3, 4])
        self.assertEqual(len({call.args[0]["controller_id"] for call in calls}), 1)
        self.assertEqual([call.kwargs["mutating"] for call in calls], [False, True, True, False])
        self.assertEqual(set(calls[0].args[0]["config"]),
                         {"plex_url", "plex_token_file", "plex_client_id", "plex_server_id"})

    def test_helper_rejects_urls_unsupported_by_native_adapter_before_runtime(self):
        for url in ("https://plex.example.test", "http://plex.example.test:32400/plex",
                    "http://[::1]:32400", "http://@plex.example.test:32400",
                    "http://plex.example.test:0", "http://plex.example.test:65536",
                    "http://plex.example.test:32400/\n", "http://plex.example.test:32400/\\"):
            with self.subTest(url=url), self.assertRaises(shortcut.ShortcutError):
                shortcut.native_config(dict(native_config(), plex_url=url))
        self.assertEqual(shortcut.native_config(dict(native_config(), plex_url="https://plex.example.test:32400/"))["plex_url"],
                         "https://plex.example.test:32400")


@unittest.skipIf(os.name == "nt", "The Bash shortcut process and lock boundary requires POSIX")
class HostBoundaryTests(unittest.TestCase):
    def test_lock_rejects_overlap_and_releases_after_failure_without_unlinking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lg2.lock"
            with shortcut.television_lock(path):
                inode = path.stat().st_ino
                with self.assertRaisesRegex(shortcut.ShortcutError, "Another LG2"), shortcut.television_lock(path):
                    self.fail("overlap accepted")
            with self.assertRaisesRegex(RuntimeError, "failure"), shortcut.television_lock(path):
                raise RuntimeError("failure")
            with shortcut.television_lock(path):
                self.assertEqual(path.stat().st_ino, inode)

    def test_lock_rejects_symlink_or_non_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lg2.lock"
            target = Path(directory) / "target"
            target.touch()
            path.symlink_to(target)
            with self.assertRaises(OSError), shortcut.television_lock(path):
                pass
            path.unlink()
            path.touch(mode=0o644)
            path.chmod(0o644)
            with self.assertRaises(shortcut.ShortcutError), shortcut.television_lock(path):
                pass

    def test_installed_symlink_is_resolved_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ott"
            path.symlink_to(ROOT / "cli/ott.py")
            resolved, api = shortcut.load_cli(path)
            self.assertEqual(resolved, ROOT / "cli/ott.py")
            self.assertTrue(callable(api.plex_queue_metadata))

    def run_script(self, content, mutating=False, native=False):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake.py"
            script.write_text(content)
            progress = shortcut.Progress(io.StringIO())
            runner = shortcut.Runner(script, progress, time.monotonic() + 3)
            if native:
                return runner.run([sys.executable, str(script)], 1, mutating=mutating, safe_rejection=True)
            return runner.call(["caps"], 1, mutating=mutating)

    def test_native_rejection_reports_only_allowlisted_numeric_code(self):
        safe = ("Native Plex rejected the Companion request (code 403); check that the native Plex app "
                "is ready and supports this operation. The request was not repeated.")
        with self.assertRaisesRegex(shortcut.ShortcutError, r"request \(code 403\)"):
            self.run_script(f"import sys; print({safe!r}, file=sys.stderr); sys.exit(1)", True, True)
        for message in (safe + " token=private", "Private URL token=private", safe.replace("403", "token=private")):
            with self.subTest(message=message), self.assertRaises(shortcut.ShortcutError) as error:
                self.run_script(f"import sys; print({message!r}, file=sys.stderr); sys.exit(1)", True, True)
            self.assertNotIn("private", str(error.exception))
            self.assertNotIn("code 403", str(error.exception))

    def test_native_timeout_advice_mentions_screen_gate_and_explicit_alternative(self):
        self.assertIn("Remote Watch Pass", shortcut.NATIVE_INSPECT)
        self.assertIn("even on the LAN", shortcut.NATIVE_INSPECT)
        self.assertIn("Use 'coco'", shortcut.NATIVE_INSPECT)
        self.assertIn("never started automatically", shortcut.NATIVE_INSPECT)

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

        with mock.patch.object(shortcut.subprocess, "Popen", side_effect=record), \
                self.assertRaisesRegex(shortcut.ShortcutError, "may have been accepted"):
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


if __name__ == "__main__":
    unittest.main()
