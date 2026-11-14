"""Multi-view uses the shipped CLI, independent group contexts and strict receipts."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_multiview_integration", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)
FIXTURE = json.loads((ROOT / "contracts/multiview-v1.json").read_text())


class Client(ott.Client):
    def __init__(self):
        self.config = {"players": {"t1": "dev-1", "t2": "dev-2"}, "groups": {"tt": ["t1", "t2"]}}
        self.credentials = {"devices": [{"id": "dev-1"}, {"id": "dev-2"}]}
        self.server, self.timeout, self.on_receipt = "https://controller.invalid", 45, None
        self.calls, self.lock = [], threading.Lock()
        self.barrier = None

    def call(self, device, action, params):
        with self.lock:
            self.calls.append((device, action, copy.deepcopy(params)))
        if action == "capabilities":
            if self.barrier:
                self.barrier.wait(5)
            return {"version": 1, "player": {"version": "test", "platform": "webos-native",
                                            "runtime": "runtime-" + device},
                    "input": [], "lifecycle": [], "playback": [], **copy.deepcopy(FIXTURE["capability"])}
        if action != "multiview":
            raise AssertionError("A multi-view command used a global action")
        if params["op"] == "status":
            result = copy.deepcopy(FIXTURE["results"]["status"])
            result["runtime"] = "runtime-" + device
            result["snapshot"]["context"]["scene"] = "scene-" + device
            result["snapshot"]["slots"][0]["source_revision"] = "source-" + device
            return result
        return {"version": 1, "op": params["op"], "runtime": "runtime-" + device,
                "binding": {k: copy.deepcopy(v) for k, v in params.items() if k not in ("version", "op", "runtime")},
                "accepted": True, "outcome": "applied"}


class MultiviewIntegrationTest(unittest.TestCase):
    def run_cli(self, words, client=None):
        client = client or Client()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=client.config), \
                mock.patch.object(ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), client

    def test_help_and_invalid_arguments_do_not_load_credentials_or_construct_transport(self):
        for tail, expected in ((["--help"], 0), (["focus", "0"], 1), (["focus", "10"], 1)):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(ott, "read_json", side_effect=AssertionError("credentials loaded")), \
                    mock.patch.object(ott, "Client", side_effect=AssertionError("transport constructed")), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(ott.main(["tt", "mv", *tail]), expected)

    def test_group_rejects_foreign_tickets_and_cursors_before_any_rpc(self):
        for tail in (["window", "1", "play", "ticket_1"], ["sources", "1", "--cursor", "cursor_1"]):
            code, _, err, client = self.run_cli(["tt", "mv", *tail])
            self.assertEqual(code, 1)
            self.assertIn("one player", err)
            self.assertEqual(client.calls, [])

    def test_group_dispatch_is_parallel_and_keeps_each_players_context(self):
        client = Client()
        client.barrier = threading.Barrier(2)
        code, out, err, _ = self.run_cli(["-j", "tt", "mv", "focus", "1"], client)
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual((result["succeeded"], result["failed"]), (2, 0))
        for device in ("dev-1", "dev-2"):
            calls = [(action, p) for d, action, p in client.calls if d == device]
            self.assertEqual([action for action, _ in calls], ["capabilities", "multiview", "multiview"])
            self.assertEqual(calls[0][1], {"multiview": True, "multiview_recovery_v1": True})
            self.assertEqual(calls[1][1]["op"], "status")
            request = calls[2][1]
            self.assertEqual(request["runtime"], "runtime-" + device)
            self.assertEqual(request["slot_id"], 0)
            self.assertEqual(request["expected"]["scene"], "scene-" + device)
            self.assertEqual(request["source_revision"], "source-" + device)
        self.assertEqual(client.timeout, 45)

    def test_raw_receipt_boundary_is_used_and_a_corrupt_response_is_never_resubmitted(self):
        for action, params, data in (
            ("multiview", FIXTURE["requests"]["status"]["params"], FIXTURE["results"]["status"]),
            ("capabilities", {"multiview": True}, {"version": 1, **FIXTURE["capability"]}),
            ("capabilities", {"multiview": True, "multiview_recovery_v1": True},
             {"version": 1, **FIXTURE["capability"]}),
        ):
            for corrupt in (False, True):
                with self.subTest(action=action, corrupt=corrupt):
                    client = ott.Client.__new__(ott.Client)
                    client.timeout, client.on_receipt, client.group_cancelled = 2, None, None
                    identity = "1" * 32
                    raw = json.dumps({"id": identity, "status": "ok", "data": data}).encode()
                    if corrupt:
                        raw = raw.replace(b'"status": "ok"', b'"status": "ok", "status": "ok"', 1)
                    client.api = mock.Mock(return_value=(202, {"id": identity, "receipt_wait_ms": 1000}))
                    client.api_raw = mock.Mock(return_value=(200, raw))
                    if corrupt:
                        with self.assertRaises(ott.RequestError):
                            client.call_receipt("dev-1", action, copy.deepcopy(params))
                    else:
                        receipt = client.call_receipt("dev-1", action, copy.deepcopy(params))
                        self.assertEqual(receipt["data"], data)
                    self.assertEqual(client.api.call_count, 1)
                    self.assertEqual(client.api_raw.call_count, 1)

    def test_recovery_capability_is_runtime_bound_and_requires_legacy_protocol(self):
        raw = {"version": 1, "player": {"version": "test", "platform": "webos-native",
                                        "runtime": "native-runtime-1"},
               "input": [], "lifecycle": [], "playback": [],
               **copy.deepcopy(FIXTURE["capability"])}
        recovery = {"version": 1, "runtime": raw["player"]["runtime"],
                    "operations": ["status", "stop", "off"], "off_modes": ["close_only"]}
        raw["multiview_recovery"] = recovery
        self.assertEqual(ott.capabilities_metadata(raw)["multiview_recovery"], recovery)
        for mutate in (lambda value: value.pop("multiview"),
                       lambda value: value["multiview_recovery"].update(runtime="other-runtime"),
                       lambda value: value["multiview_recovery"].update(version=True),
                       lambda value: value["multiview_recovery"].update(private="untrusted")):
            invalid = copy.deepcopy(raw)
            mutate(invalid)
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(invalid)

    def test_zero_admission_help_exposes_only_negotiated_recovery(self):
        raw = {"version": 1, "player": {"version": "test", "platform": "webos-native",
                                        "runtime": "native-runtime-1"},
               "input": [], "lifecycle": [], "playback": [],
               **copy.deepcopy(FIXTURE["capability"])}
        raw["multiview"] = {"version": 1, "operations": ["status"], "max_slots": 0,
                            "layouts": [], "source_kinds": [], "audio_policies": [], "off_modes": []}
        def render(value):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ott.print_player_help({"capabilities": ott.capabilities_metadata(value)}, "l")
            return output.getvalue()
        legacy = render(raw)
        self.assertNotIn("mv off close_only", legacy)
        self.assertNotIn("mv window N stop", legacy)
        raw["multiview_recovery"] = {"version": 1, "runtime": raw["player"]["runtime"],
                                     "operations": ["status", "stop", "off"], "off_modes": ["close_only"]}
        recovered = render(raw)
        self.assertIn("mv off close_only", recovered)
        self.assertIn("mv window N stop", recovered)
        self.assertNotIn("mv layout", recovered)

    def test_plain_status_negotiates_recovery_help_without_mutation(self):
        raw = {"version": 1, "player": {"version": "test", "platform": "webos-native",
                                        "runtime": "native-runtime-1"},
               "input": [], "lifecycle": [], "playback": [],
               **copy.deepcopy(FIXTURE["capability"])}
        raw["multiview"] = {"version": 1, "operations": ["status"], "max_slots": 0,
                            "layouts": [], "source_kinds": [], "audio_policies": [], "off_modes": []}
        recovery = {"version": 1, "runtime": "native-runtime-1",
                    "operations": ["status", "stop", "off"], "off_modes": ["close_only"]}
        negotiated = dict(copy.deepcopy(raw), multiview_recovery=recovery)
        for reply in (negotiated, ott.HTTPError(400)):
            with self.subTest(legacy_fallback=isinstance(reply, Exception)):
                # Even an old relay leaking a native marker does not establish
                # negotiation. The explicit second request decides visibility.
                client = mock.Mock(timeout=45)
                client.call.side_effect = [copy.deepcopy(negotiated), reply]
                status = {"player": copy.deepcopy(raw["player"])}
                data = ott.status_capabilities(client, "dev", status, time.monotonic() + 45)
                self.assertEqual(client.call.call_args_list, [
                    mock.call("dev", "capabilities", {}),
                    mock.call("dev", "capabilities", {"multiview": True, "multiview_recovery_v1": True})])
                self.assertEqual("multiview_recovery" in data["capabilities"], not isinstance(reply, Exception))
                self.assertEqual(client.timeout, 45)
                self.assertEqual(status, {"player": raw["player"]})
                supplied = ott.capabilities_metadata(copy.deepcopy(negotiated))
                before = copy.deepcopy(supplied)
                client.call.reset_mock()
                client.call.side_effect = [reply]
                detailed = ott.status_capabilities(client, "dev", status, time.monotonic() + 45,
                                                   controls=supplied)
                self.assertEqual(client.call.call_args_list, [mock.call(
                    "dev", "capabilities", {"multiview": True, "multiview_recovery_v1": True})])
                self.assertEqual("multiview_recovery" in detailed["capabilities"],
                                 not isinstance(reply, Exception))
                self.assertEqual(supplied, before)

    def test_recovery_help_never_passes_a_negative_remaining_timeout(self):
        raw = {"version": 1, "player": {"version": "test", "platform": "webos-native",
                                        "runtime": "native-runtime-1"},
               "input": [], "lifecycle": [], "playback": [],
               **copy.deepcopy(FIXTURE["capability"])}
        controls = ott.capabilities_metadata(raw)
        client = mock.Mock(timeout=45)
        def call(*args):
            self.assertGreater(client.timeout, 0)
            return copy.deepcopy(raw)
        client.call.side_effect = call
        with mock.patch.object(ott.time, "monotonic", side_effect=[0, 0.9, 1.1, 1.2]):
            data = ott.status_capabilities(client, "dev", {"player": raw["player"]}, 1,
                                           controls=controls)
        self.assertIsNone(data["capabilities"])
        self.assertIn("within --timeout", data["capabilities_error"])
        self.assertEqual(client.timeout, 45)

    def test_complete_cli_copy_loads_module_without_checkout_or_credentials(self):
        with tempfile.TemporaryDirectory(prefix="ott-multiview-cli-") as temporary:
            target = Path(temporary)
            for source in (ROOT / "cli").glob("*.py"):
                shutil.copy2(source, target / source.name)
            command = [sys.executable, "-B", str(target / "ott.py"), "-c", str(target / "missing.json")]
            # Keep the copied CLI's Unicode help deterministic on Windows pipes.
            environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run([*command, "fixture", "mv", "--help"], cwd=target,
                                    capture_output=True, encoding="utf-8", env=environment, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Windows are numbered 1 through 9", result.stdout)
            (target / "multiview.py").unlink()
            result = subprocess.run([*command, "fixture", "mv", "--help"], cwd=target,
                                    capture_output=True, encoding="utf-8", env=environment, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Install multiview.py beside ott.py", result.stderr)
            result = subprocess.run([*command, "--help"], cwd=target,
                                    capture_output=True, encoding="utf-8", env=environment, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
