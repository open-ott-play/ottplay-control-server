"""System television controls remain distinct from the player RPC lane."""
import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ott_television_cli_test", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
tv = ott.television_module()


class TelevisionCliTest(unittest.TestCase):
    def client(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {"players": {"l": "living", "lg2": "bedroom", "living_alias": "living"}}
        client.credentials = {"devices": [{"id": "living"}, {"id": "bedroom"}]}
        client.timeout, client.on_receipt = 45, None
        client.api = mock.Mock(side_effect=self.reply)
        client.call = mock.Mock(side_effect=ott.Error("Player is disconnected"))
        return client

    def reply(self, path, payload=None, **_):
        if payload is None:
            device = "bedroom" if "bedroom" in path else "living"
            return 200, {"version": 1, "device_id": device, "configured": True,
                         "capabilities": sorted(tv.ACTIONS), "paired": True, "fallback_configured": True}
        action = payload["action"]
        result = {"version": 1, "device_id": payload["device_id"], "action": action,
                  "transport": "webos", "outcome": "confirmed"}
        if action == "pair":
            result["pairing"] = {"paired": True}
        elif action == "apps":
            result["apps"] = [{"id": "com.webos.app.browser", "title": "Web Browser"}]
        else:
            result["state"] = {"power": "off" if action == "off" else "on",
                               "volume": payload.get("volume", 25), "muted": payload.get("muted", False),
                               "app_id": "com.webos.app.browser"}
        return 200, result

    def run_cli(self, words, client=None):
        client = client or self.client()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=client.config), \
                mock.patch.object(ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), client

    def test_commands_and_aliases_use_only_television_endpoint(self):
        cases = [([], {"action": "status"}), (["status"], {"action": "status"}),
                 (["volume"], {"action": "status"}), (["volume", "0"], {"action": "volume", "volume": 0}),
                 (["VOL", "100"], {"action": "volume", "volume": 100}),
                 (["volume", "+5"], {"action": "volume", "delta": 5}),
                 (["v", "-5"], {"action": "volume", "delta": -5}),
                 (["mute", "ON"], {"action": "mute", "muted": True}),
                 (["mute", "off"], {"action": "mute", "muted": False}),
                 (["wake"], {"action": "on"}), (["ON"], {"action": "on"}),
                 (["off"], {"action": "off"}), (["launch"], {"action": "launch"}),
                 (["go"], {"action": "go"}), (["start"], {"action": "go"}),
                 (["go", "plex"], {"action": "go", "app": "plex"}),
                 (["start", "Plex"], {"action": "go", "app": "plex"}),
                 (["launch", "media-station_x"], {"action": "launch", "app": "media-station_x"}),
                 (["apps"], {"action": "apps"}), (["pair"], {"action": "pair"})]
        for words, params in cases:
            with self.subTest(words=words):
                code, out, _, client = self.run_cli(["-j", "l", "TV", *words])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["action"], params["action"])
                client.api.assert_called_once_with("/api/televisions", {"device_id": "living", **params, "timeout_ms": 44000},
                                                   timeout=45, timeout_cap=120)
                client.call.assert_not_called()

    def test_invalid_syntax_never_sends_any_request(self):
        invalid = [["volume", "-0"], ["volume", "+0"], ["volume", "101"], ["volume", "+101"],
                   ["volume", "1.5"], ["volume", "nan"], ["volume", "01"], ["mute"], ["mute", "toggle"],
                   ["off", "now"], ["launch", "https://private.example"], ["pair", "secret"], ["power"],
                   ["go", "plex", "extra"], ["go", "../plex"], ["go", "com.plex.app"],
                   ["go", "a" * 33], ["on", "plex"],
                   ["shell", "anything"], ["status", "extra"]]
        for words in invalid:
            with self.subTest(words=words):
                code, out, _, client = self.run_cli(["l", "tv", *words])
                self.assertEqual((code, out), (1, ""))
                client.api.assert_not_called()
                client.call.assert_not_called()

    def test_deadline_is_explicit_bounded_and_allows_long_pairing(self):
        code, _, _, client = self.run_cli(["-t", "90", "l", "tv", "pair"])
        self.assertEqual(code, 0)
        self.assertEqual(client.api.call_args.kwargs, {"timeout": 90, "timeout_cap": 120})
        self.assertEqual(client.api.call_args.args[1]["timeout_ms"], 89000)
        for timeout, operation_ms in ((1, 900), (5, 4500), (10, 9000), (120, 119000)):
            code, _, _, client = self.run_cli(["-t", str(timeout), "l", "tv", "status"])
            self.assertEqual(code, 0)
            self.assertEqual(client.api.call_args.kwargs["timeout"], timeout)
            self.assertEqual(client.api.call_args.args[1]["timeout_ms"], operation_ms)
        for value in ("0", "-1", "121", "nan", "inf"):
            code, _, _, client = self.run_cli(["-t", value, "l", "tv", "go"])
            self.assertEqual(code, 1)
            client.api.assert_not_called()

    def test_http_cap_opt_in_preserves_default_and_total_deadline(self):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 90
        client._api = mock.Mock(return_value=(200, {}))
        client.api("/ordinary", timeout=90)
        self.assertEqual(client._api.call_args.args[2], 10)
        client.api("/api/televisions", timeout=90, timeout_cap=120)
        self.assertEqual(client._api.call_args.args[2], 90)
        for cap in (True, 0, 121, 10**1000, float("inf"), "90"):
            with self.assertRaises(ott.Error):
                client.api("/api/televisions", timeout_cap=cap)

    def test_control_and_pairing_secrets_are_never_projected(self):
        client = self.client()
        raw = self.reply("", {"device_id": "living", "action": "status"})[1]
        raw.update(client_key="SECRET", address="http://secret/", config={"token": "SECRET"})
        raw["state"]["url"] = "http://secret/"
        client.api.return_value = (200, raw)
        client.api.side_effect = None
        code, out, err, _ = self.run_cli(["-j", "l", "tv"], client)
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("SECRET", out)
        self.assertNotIn("secret", out)

    def test_wrong_identity_and_invalid_mutation_receipts_never_claim_success(self):
        base = self.reply("", {"device_id": "living", "action": "volume", "volume": 20})[1]
        invalid = [None, [], {}, {**base, "version": True}, {**base, "device_id": "bedroom"},
                   {**base, "action": "off"}, {**base, "transport": []}, {**base, "outcome": "done"},
                   {**base, "state": {"volume": True}}, {**base, "state": {"volume": 21}},
                   {**base, "state": {"volume": float("nan")}}, {**base, "state": {"volume": 10**1000}},
                   {**base, "state": {}}]
        for raw in invalid:
            client = self.client()
            client.api.side_effect = None
            client.api.return_value = 200, raw
            code, out, err, client = self.run_cli(["l", "tv", "volume", "20"], client)
            self.assertEqual((code, out, client.api.call_count), (1, "", 1))
            self.assertIn("may have executed", err)

    def test_app_identifiers_match_controller_limit_and_null_apps_are_optional(self):
        for action in ("status", "apps"):
            for length, expected_code in ((256, 0), (257, 1)):
                with self.subTest(action=action, length=length):
                    raw = self.reply("", {"device_id": "living", "action": action})[1]
                    if action == "apps":
                        raw["apps"][0]["id"] = "a" * length
                    else:
                        raw["state"]["app_id"] = "a" * length
                        raw["apps"] = None
                    client = self.client()
                    client.api.side_effect = None
                    client.api.return_value = 200, raw
                    code, _, _, _ = self.run_cli(["l", "tv", action], client)
                    self.assertEqual(code, expected_code)

    def test_logical_error_text_is_sanitized_and_not_retried(self):
        client = self.client()
        client.api.side_effect = None
        client.api.return_value = 200, {"version": 1, "device_id": "living", "action": "volume",
                                        "code": "command_rejected", "error": "SECRET http://private/", "uncertain": True}
        code, out, err, client = self.run_cli(["l", "tv", "volume", "+5"], client)
        self.assertEqual((code, out, client.api.call_count), (1, "", 1))
        self.assertIn("may have executed", err)
        self.assertNotIn("SECRET", err)
        self.assertNotIn("private", err)

    def test_unknown_app_reports_configuration_error_without_retry(self):
        client = self.client()
        client.api.side_effect = None
        client.api.return_value = 200, {"version": 1, "device_id": "living", "action": "go",
                                        "code": "app_not_configured", "uncertain": False}
        code, out, err, client = self.run_cli(["l", "tv", "go", "plex"], client)
        self.assertEqual((code, out, client.api.call_count), (1, "", 1))
        self.assertIn("app_aliases", err)
        self.assertNotIn("may have executed", err)
        client.call.assert_not_called()

    def test_transport_failure_does_not_replay_relative_change_or_call_player(self):
        client = self.client()
        client.api.side_effect = ott.TransportError("SECRET", "deadline")
        code, out, err, client = self.run_cli(["l", "tv", "volume", "+5"], client)
        self.assertEqual((code, out, client.api.call_count), (1, "", 1))
        self.assertIn("may have executed", err)
        self.assertNotIn("SECRET", err)
        client.call.assert_not_called()

    def test_accepted_and_uncertain_results_are_not_confirmed(self):
        for outcome, code in (("accepted", 0), ("uncertain", 1)):
            client = self.client()
            client.api.side_effect = None
            client.api.return_value = 200, {"version": 1, "device_id": "living", "action": "off",
                                            "transport": "webos", "outcome": outcome}
            actual, out, err, client = self.run_cli(["l", "tv", "off"], client)
            self.assertEqual((actual, client.api.call_count), (code, 1))
            self.assertIn(outcome, out)
            if outcome == "uncertain":
                self.assertIn("not repeated", err)
            else:
                self.assertIn("has not been confirmed", out)

    def test_fallback_route_is_explicit_in_output(self):
        client = self.client()
        raw = self.reply("", {"device_id": "living", "action": "status"})[1]
        raw["transport"] = "home_assistant"
        client.api.side_effect = None
        client.api.return_value = 200, raw
        code, out, _, _ = self.run_cli(["l", "tv"], client)
        self.assertEqual(code, 0)
        self.assertIn("Home Assistant fallback", out)

    def test_groups_deduplicate_and_pairing_refuses_multiple_tvs(self):
        code, out, err, client = self.run_cli(["-j", "l,lg2,living_alias", "tv", "off"])
        self.assertEqual((code, err, client.api.call_count), (0, "", 2))
        self.assertEqual(json.loads(out)["succeeded"], 2)
        self.assertEqual([call.args[1]["device_id"] for call in client.api.call_args_list], ["living", "bedroom"])
        code, out, err, client = self.run_cli(["l,lg2", "tv", "pair"])
        self.assertEqual((code, out, client.api.call_count), (1, "", 0))
        self.assertIn("one player", err)

    def test_group_failure_does_not_stop_other_tv(self):
        client = self.client()
        def reply(path, payload=None, **kwargs):
            if payload["device_id"] == "living":
                raise ott.TransportError("unavailable", "deadline")
            return self.reply(path, payload, **kwargs)
        client.api.side_effect = reply
        code, out, _, client = self.run_cli(["-j", "l,lg2", "tv", "on"], client)
        result = json.loads(out)
        self.assertEqual((code, result["succeeded"], result["failed"], client.api.call_count), (1, 1, 1, 2))

    def test_help_works_without_credentials_or_a_controller(self):
        out = io.StringIO()
        with mock.patch.object(ott, "read_json", side_effect=AssertionError("No configuration needed")), contextlib.redirect_stdout(out):
            self.assertEqual(ott.main(["l", "tv", "--help"]), 0)
        self.assertIn("ott l tv", out.getvalue())

    def test_overview_shows_tv_help_even_if_player_disconnected(self):
        code, out, err, client = self.run_cli(["l"])
        self.assertEqual(code, 1)
        self.assertIn("Television controls", out)
        self.assertIn("ott l tv on / off", out)
        self.assertIn("Player is disconnected", err)
        self.assertEqual(client.api.call_count, 1)

    def test_overview_discovery_shares_status_deadline_and_restores_timeout(self):
        client = self.client()
        observed = []
        def status(*_):
            observed.append(client.timeout)
            raise ott.Error("Player is disconnected")
        client.call.side_effect = status
        with mock.patch.object(ott.time, "monotonic", side_effect=[10, 12]):
            code, _, _, _ = self.run_cli(["l"], client)
        self.assertEqual((code, observed, client.timeout), (1, [43], 45))

    def test_old_controller_or_unconfigured_binding_does_not_show_tv_help(self):
        for response in (ott.HTTPError(404), (200, {"version": 1, "device_id": "living", "configured": False})):
            client = self.client()
            client.api.side_effect = response if isinstance(response, Exception) else None
            if not isinstance(response, Exception):
                client.api.return_value = response
            _, out, _, _ = self.run_cli(["l"], client)
            self.assertNotIn("Television controls", out)

    def test_player_lifecycle_and_volume_keep_the_existing_lane(self):
        for words in (("standby",), ("go",), ("restart",), ("v", "20")):
            _, _, _, client = self.run_cli(["l", *words])
            client.api.assert_not_called()
            client.call.assert_called_once()

    def test_confirmation_must_have_operation_specific_evidence(self):
        for action, state in (("on", {"power": "off"}), ("off", {"power": "on"}),
                              ("launch", {}), ("go", {}), ("mute", {"muted": True})):
            client = self.client()
            raw = {"version": 1, "device_id": "living", "action": action,
                   "transport": "webos", "outcome": "confirmed", "state": state}
            client.api.side_effect = None
            client.api.return_value = 200, copy.deepcopy(raw)
            words = [action, "off"] if action == "mute" else [action]
            code, out, err, _ = self.run_cli(["l", "tv", *words], client)
            self.assertEqual((code, out), (1, ""))
            self.assertIn("may have executed", err)


if __name__ == "__main__":
    unittest.main()
