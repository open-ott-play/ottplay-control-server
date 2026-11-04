"""Shutdown is an explicit device route, including parallel player groups."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ott_shutdown_cli", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class ShutdownCliTest(unittest.TestCase):
    def client(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {"players": {"p1": "projector1", "p2": "projector2"},
                         "groups": {"projectors": ["p1", "p2"]}}
        client.credentials = {"devices": [{"id": "projector1"}, {"id": "projector2"}]}
        client.timeout, client.on_receipt = 10, None
        client.api = mock.Mock(side_effect=AssertionError("No controller access expected"))
        client.call = mock.Mock(side_effect=AssertionError("No player RPC expected"))
        return client

    def run_cli(self, words, client=None):
        client = client or self.client()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=client.config), \
                mock.patch.object(ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), client

    def test_help_needs_no_configuration_or_controller(self):
        for spelling in ("--help", "-h", "help"):
            with mock.patch.object(ott, "Client") as constructor, \
                    mock.patch.object(ott, "read_json") as read, \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(ott.main(["p2", "shutdown", spelling]), 0)
            constructor.assert_not_called()
            read.assert_not_called()
            self.assertIn("shutdown", out.getvalue())

    def test_invalid_arguments_are_rejected_before_client_creation(self):
        for tail in (["now"], ["device", "again"], ["status", "extra"], ["bind-adb"]):
            with mock.patch.object(ott, "Client") as constructor, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ott.main(["p2", "shutdown", *tail]), 1)
            constructor.assert_not_called()

    def test_single_target_uses_device_route(self):
        module = ott.device_power_module()
        with mock.patch.object(module, "run", return_value=0) as run, \
                mock.patch.object(ott, "device_power_module", return_value=module):
            code, _, _, client = self.run_cli(["p2", "shutdown"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[4:7], ("projector2", "p2", []))
        client.call.assert_not_called()
        client.api.assert_not_called()

    def test_group_binding_changes_are_rejected_before_any_target(self):
        module = ott.device_power_module()
        for selector in ("p1,p2", "projectors"):
            for tail in (["bind-adb", "192.0.2.20:5555"], ["unbind"]):
                with mock.patch.object(module, "run") as run, \
                        mock.patch.object(ott, "device_power_module", return_value=module):
                    code, _, err, _ = self.run_cli([selector, "shutdown", *tail])
                self.assertEqual(code, 1)
                self.assertIn("exactly one player", err)
                run.assert_not_called()

    def test_group_shutdown_is_parallel_and_preserves_partial_results(self):
        for selector in ("p1,p2", "projectors"):
            barrier = threading.Barrier(2, timeout=2)
            module = ott.device_power_module()

            def execute(_api, _client, _config, _path, device, _name, _words, _timeout, _json):
                barrier.wait()
                if device == "projector2":
                    raise ott.Error("Device is unavailable; no shutdown was sent")
                print(json.dumps({"action": "shutdown", "outcome": "accepted"}))
                return 0

            with mock.patch.object(module, "run", side_effect=execute), \
                    mock.patch.object(ott, "device_power_module", return_value=module):
                code, out, _, client = self.run_cli(["-j", selector, "shutdown"])
            result = json.loads(out)
            self.assertEqual(code, 1)
            self.assertEqual((result["succeeded"], result["failed"]), (1, 1))
            self.assertEqual([r["player"] for r in result["results"]], ["p1", "p2"])
            client.call.assert_not_called()
            client.api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
