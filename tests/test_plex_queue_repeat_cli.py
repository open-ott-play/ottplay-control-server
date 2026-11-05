"""Ordinary Plex repeat is additive, negotiated, and bound to one live queue."""
import contextlib
import copy
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_plex_queue_repeat", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)
CONTRACT = json.loads((ROOT / "contracts/plex-queue-repeat-v1.json").read_text())
V1 = json.loads((ROOT / "contracts/plex-queue-v1.json").read_text())
V2 = json.loads((ROOT / "contracts/plex-queue-state-v2.json").read_text())


def caps(version=2, runtime="page-123"):
    result = {
        "version": 1, "player": {"version": "test", "platform": "tauri", "runtime": runtime},
        "lifecycle": [], "input": [], "playback": [],
        "plex_queue": copy.deepcopy(V1["capability"]),
        "plex_queue_repeat": copy.deepcopy(CONTRACT["capability"]),
    }
    if version == 2:
        result["plex_queue_state_v2"] = copy.deepcopy(V2["capability"])
    return result


def state(version=2, mode="all", runtime="page-123"):
    result = copy.deepcopy(CONTRACT["repeat_response"])
    result.update(version=version, repeat=mode, runtime=runtime)
    return result


class PlexQueueRepeatTests(unittest.TestCase):
    def run_cli(self, words, responses, *, plain=False):
        client = mock.Mock(timeout=45)
        client.device.return_value = "dev_tv"
        client.call.side_effect = responses
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "Client", return_value=client), \
                mock.patch.object(ott, "read_json", return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main([*([] if plain else ["--json"]), "tv", *words])
        self.assertEqual(client.timeout, 45)
        return code, output.getvalue(), errors.getvalue(), client.call.call_args_list

    def test_shared_contract_and_aliases_do_not_extend_frozen_operations(self):
        self.assertEqual(CONTRACT["capability"], {"version": 1, "modes": ["none", "all"]})
        self.assertEqual(V1["operations"], CONTRACT["unchanged_base_operations"])
        self.assertEqual(V2["capability"]["operations"], CONTRACT["unchanged_v2_operations"])
        self.assertEqual(ott.PLEX_QUEUE_OPERATIONS, set(V1["operations"]))
        self.assertEqual(ott.PLEX_QUEUE_STATE_OPERATIONS, set(V2["capability"]["operations"]))
        self.assertEqual(ott.parse_command(["plex", "repeat"]), ("plex_queue", {"op": "status"}))
        for alias, mode in CONTRACT["cli_aliases"].items():
            for spelling in (alias, alias.upper()):
                self.assertEqual(ott.parse_command(["plex", "repeat", spelling]),
                                 ("plex_queue", {"op": "repeat", "repeat": mode}))
        for words in (["plex", "repeat", "one"], ["plex", "repeat", "all", "save"]):
            code, out, _, calls = self.run_cli(words, [])
            self.assertEqual((code, out, calls), (1, "", []))

    def test_each_mutation_negotiates_repeat_and_optional_v2_once(self):
        for version in (1, 2):
            for alias, mode in CONTRACT["cli_aliases"].items():
                result = dict(state(version, mode), state="paused")
                confirmed = caps(version)
                confirmed["plex_queue_repeat"]["modes"].reverse()
                probe = {"plex_queue_repeat": True}
                params = {"op": "repeat", "repeat": mode, "runtime": "page-123"}
                if version == 2:
                    probe["plex_queue_state_v2"] = True
                    params["state_version"] = 2
                with self.subTest(version=version, alias=alias):
                    code, out, err, calls = self.run_cli(["plex", "repeat", alias], [caps(version), confirmed, result])
                    self.assertEqual((code, err, json.loads(out)), (0, "", result))
                    self.assertEqual(calls, [mock.call("dev_tv", "capabilities", {}),
                                            mock.call("dev_tv", "capabilities", probe),
                                            mock.call("dev_tv", "plex_queue", params)])

    def test_bare_repeat_is_read_only_and_works_without_repeat_support(self):
        legacy = caps(1)
        del legacy["plex_queue_repeat"]
        code, out, err, calls = self.run_cli(["plex", "repeat"], [legacy, state(1, "none")], plain=True)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("repeat=none", out)
        self.assertEqual(calls, [mock.call("dev_tv", "capabilities", {}),
                                mock.call("dev_tv", "plex_queue", {"op": "status", "runtime": "page-123"})])

    def test_missing_malformed_or_unbound_marker_never_sends_a_mutation(self):
        malformed = [None, {}, {"version": True, "modes": ["none", "all"]},
                     {"version": 1.0, "modes": ["none", "all"]},
                     {"version": 1, "modes": ["all", "all"]},
                     {"version": 1, "modes": ["none", "one"]},
                     {"version": 1, "modes": ["none", {}]},
                     dict(CONTRACT["capability"], extra=True)]
        cases = []
        absent = caps(1)
        del absent["plex_queue_repeat"]
        cases.append(absent)
        for marker in malformed:
            cases.append(dict(caps(1), plex_queue_repeat=marker))
        no_base = caps(1)
        del no_base["plex_queue"]
        cases.append(no_base)
        no_status = caps(1)
        no_status["plex_queue"]["operations"] = ["play"]
        cases.append(no_status)
        cases.append(caps(1, "invalid/runtime"))
        for value in cases:
            with self.subTest(value=value):
                code, out, err, calls = self.run_cli(["plex", "repeat", "on"], [value])
                self.assertEqual((code, out, len(calls)), (1, "", 1))
                self.assertTrue(err)
                self.assertEqual(calls[0].args[1], "capabilities")

    def test_old_controller_or_changed_negotiation_never_receives_mutation(self):
        for version in (1, 2):
            absent = caps(version)
            del absent["plex_queue_repeat"]
            malformed = caps(version)
            malformed["plex_queue_repeat"]["modes"] = ["none"]
            old_controller = ott.HTTPError(400)
            failures = [old_controller, absent, malformed, caps(version, "page-new"),
                        ott.PlayerUnsupported("unsupported")]
            if version == 2:
                failures.append(caps(1))
            for response in failures:
                with self.subTest(version=version, response=type(response).__name__):
                    code, out, err, calls = self.run_cli(["plex", "repeat", "all"], [caps(version), response])
                    self.assertEqual((code, out, len(calls)), (1, "", 2))
                    self.assertTrue(all(call.args[1] == "capabilities" for call in calls))
                    self.assertTrue(err)
                    if response is old_controller:
                        self.assertIn("controller does not support Plex repeat", err)
                        self.assertIn("No queue command was sent", err)

    def test_wrong_mode_inactive_or_stale_result_is_not_success_or_retried(self):
        for version in (1, 2):
            failures = [state(version, "none"), state(3 - version), state(version, runtime="page-other"),
                        dict(state(version), active=False, state="idle", ids=[], index=None),
                        ott.HTTPError(400), ott.RequestError("request_pending_timeout", "receipt", "Acceptance is uncertain")]
            for result in failures:
                with self.subTest(version=version, result=type(result).__name__):
                    code, out, err, calls = self.run_cli(["plex", "repeat", "all"], [caps(version), caps(version), result])
                    self.assertEqual((code, out, len(calls)), (1, "", 3))
                    self.assertTrue(err)
                    self.assertEqual(sum(call.args[1] == "plex_queue" for call in calls), 1)

    def test_fixed_refusals_are_reported_without_replay_or_private_details(self):
        for message in [*CONTRACT["refusals"].values(), "private-server-token"]:
            failure = ott.PlayerRejected("private-server-token", {"error": message})
            code, out, err, calls = self.run_cli(["plex", "repeat", "all"], [caps(), caps(), failure])
            self.assertEqual((code, out, len(calls)), (1, "", 3))
            self.assertIn("The request was not repeated", err)
            self.assertNotIn("private-server-token", err)
            if message != "private-server-token":
                self.assertIn(message, err)

    def test_common_fences_and_unknown_private_refusals_use_generic_output_without_retry(self):
        messages = [*CONTRACT["common_fence_refusals"].values(),
                    "private fixture https://private.example/?token=private-server-token /Users/private"]
        for version in (1, 2):
            for plain in (False, True):
                for rejected in (ott.PlayerRejected, ott.PlayerUnsupported):
                    for message in messages:
                        with self.subTest(version=version, plain=plain, status=rejected.__name__, message=message):
                            failure = rejected(message, {"error": message})
                            code, out, err, calls = self.run_cli(
                                ["plex", "repeat", "all"], [caps(version), caps(version), failure], plain=plain)
                            self.assertEqual((code, out, len(calls)), (1, "", 3))
                            self.assertEqual(sum(call.args[1] == "plex_queue" for call in calls), 1)
                            self.assertIn("The request was not repeated", err)
                            self.assertNotIn(message, err)
                            self.assertNotIn("private-server-token", err)
                            self.assertNotIn("private.example", err)
                            self.assertNotIn("/Users/private", err)

    def test_repeat_help_is_advertised_only_by_its_own_capability(self):
        for present in (False, True):
            value = caps(1)
            if not present:
                del value["plex_queue_repeat"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ott.print_player_help({"capabilities": ott.capabilities_metadata(value)}, "tv")
            self.assertEqual("tv plex repeat [off|none|all|on]" in output.getvalue(), present)


class PlexQueueRepeatGroupTests(unittest.TestCase):
    def test_group_negotiates_each_player_and_isolates_an_old_controller(self):
        records, lock = [], threading.Lock()

        class GroupClient(ott.Client):
            def __init__(self):
                self.config = {"players": {f"t{i}": f"dev_{i}" for i in range(1, 4)}}
                self.credentials = {"devices": [{"id": value} for value in self.config["players"].values()]}
                self.server, self.timeout, self.on_receipt = "https://controller.example", 45, None

            def call(self, device, action, params):
                with lock:
                    records.append((device, action, copy.deepcopy(params)))
                version = 1 if device == "dev_1" else 2
                runtime = device.replace("_", "-")
                if action == "capabilities":
                    if device == "dev_3" and params:
                        raise ott.HTTPError(400)
                    return caps(version, runtime)
                if action == "plex_queue":
                    return state(version, "all", runtime)
                raise AssertionError((device, action, params))

        client, output, errors = GroupClient(), io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "Client", return_value=client), \
                mock.patch.object(ott, "read_json", return_value=client.config), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(["--json", "t1,t2,t3", "plex", "repeat", "on"])
        self.assertEqual((code, errors.getvalue()), (1, ""))
        rows = json.loads(output.getvalue())["results"]
        self.assertEqual([row["ok"] for row in rows], [True, True, False])
        self.assertIn("controller does not support Plex repeat", rows[2]["error"])
        for device in ("dev_1", "dev_2", "dev_3"):
            calls = [(action, params) for target, action, params in records if target == device]
            probe = {"plex_queue_repeat": True}
            if device != "dev_1":
                probe["plex_queue_state_v2"] = True
            expected = [("capabilities", {}), ("capabilities", probe)]
            if device != "dev_3":
                params = {"op": "repeat", "repeat": "all", "runtime": device.replace("_", "-")}
                if device == "dev_2":
                    params["state_version"] = 2
                expected.append(("plex_queue", params))
            self.assertEqual(calls, expected)


if __name__ == "__main__":
    unittest.main()
