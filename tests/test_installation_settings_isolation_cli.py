"""Installation trust cannot travel in portable presets or Local profiles."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import test_offline_profiles_cli as local_profiles
import test_player_settings_cli as settings_cli
import test_presets_cli as presets
import test_native_provider_presets_cli as native_presets


SETTING = "nativeMultiviewService"
BEARER = "fixture-installation-bearer-never-printed"
SERVICE = json.dumps({"server_id": "fixture-service", "origin": "https://service.example.invalid",
                      "audience": "fixture-audience", "installation_id": "fixture-installation",
                      "control_bearer": BEARER}, separators=(",", ":"))


def sensitive_responses(configured=True):
    get, saved = settings_cli.result("get"), settings_cli.result("set")
    get["descriptor"] = {
        "id": SETTING, "type": "string", "scope": "application", "sensitive": True,
        "access": {"read": False, "write": True}, "constraints": {"max_utf8_bytes": 4096},
        "apply": "immediate",
    }
    for data in (get, saved):
        data["context"].update(provider=None, profile=None)
        data["setting"] = {"id": SETTING, "revision": "revision-1", "storage": "stored",
                           "redacted": True, "configured": configured}
    saved["application"] = {"state": "completed"}
    return get, saved


class GroupClient(settings_cli.ott.Client):
    """Real group dispatch with only in-memory setting responses."""
    def __init__(self):
        self.config = {"players": {"f": "device-f", "g": "device-g"}, "groups": {"both": ["f", "g"]}}
        self.credentials = {"devices": [{"id": "device-f"}, {"id": "device-g"}]}
        self.timeout, self.on_receipt = 45, None
        self.calls, self.lock = [], threading.Lock()

    def call(self, device, action, params):
        with self.lock:
            self.calls.append((device, action, copy.deepcopy(params)))
        if action == "capabilities":
            return settings_cli.caps()
        if action != "player_settings":
            raise AssertionError("Unexpected group action")
        if params["id"] == SETTING:
            if params["op"] != "get":
                raise AssertionError("An installation credential reached a group write")
            return sensitive_responses()[0]
        if params["id"] != "bufSize":
            raise AssertionError("Unexpected group setting")
        return settings_cli.result(params["op"])


class InstallationSettingsIsolationTest(unittest.TestCase):
    def assert_private(self, *outputs):
        for output in outputs:
            self.assertNotIn(BEARER, output)
            self.assertNotIn(SERVICE, output)

    def group_cli(self, words):
        client = GroupClient()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(settings_cli.ott, "read_json", return_value=client.config), \
                mock.patch.object(settings_cli.ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = settings_cli.ott.main(words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client

    def test_portable_presets_refuse_installation_key_before_any_rpc(self):
        for value in (SERVICE, ""):
            invalid = []
            for section in (None, "plex", "m3u"):
                config = presets.config()
                destination = config["presets"]["local"]
                if section == "m3u":
                    destination = destination[section][0]
                elif section is not None:
                    destination = destination[section]
                destination[SETTING] = value
                invalid.append(config)
            config = presets.config()
            config["presets"]["local"]["settings"] = {SETTING: value}
            invalid.append(config)
            for provider, credentials in (("edem", {"key": "fixture-key"}),
                                          ("d/maxtv", {"password": "fixture-password"})):
                invalid.append(native_presets.configuration([
                    {"provider": provider, "number": 1, "settings": {**credentials, SETTING: value}},
                ]))
            for index, config in enumerate(invalid):
                before = copy.deepcopy(config)
                for mode in ([], ["plan"], ["verify"]):
                    with self.subTest(location=index, empty=not value, mode=mode):
                        code, out, err, player, _ = presets.run(
                            config, words=["tv", "load", "local", *mode])
                        self.assertEqual(code, 1)
                        self.assertEqual(player.calls, [])
                        self.assertEqual(config, before)
                        self.assert_private(out, err)

    def test_provider_credentials_remain_supported_without_installation_trust(self):
        config, player = native_presets.configuration(), native_presets.NativePlayer()
        code, out, err, player, _ = presets.run(config, player=player)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["status"], "loaded")
        self.assertEqual({row["provider"] for row in player.native_saves}, {"edem", "d/maxtv"})
        self.assertTrue(any(action == "provider_settings" and params.get("provider") == "plex"
                            for _, action, params in player.calls))
        self.assertFalse(any(action == "player_settings" for _, action, _ in player.calls))
        for private in (presets.TOKEN, native_presets.PRIVATE):
            self.assertNotIn(private, out + err)

    def test_local_profile_import_refuses_installation_key_without_rpc(self):
        with tempfile.TemporaryDirectory(prefix="ott-installation-import-") as temporary:
            path = Path(temporary) / "profile.json"
            for location in ("profile", "source", "selected"):
                document = local_profiles.document()
                destination = (document if location == "profile" else document["sources"][0]
                               if location == "source" else document["selected"])
                destination[SETTING] = SERVICE
                path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
                with self.subTest(location=location):
                    code, out, err, client = local_profiles.OfflineProfileTest.run_cli(
                        self, ["profile", "import", "1", str(path)], [])
                    self.assertEqual((code, out, client.call.call_count), (1, "", 0))
                    self.assert_private(err)

    def test_local_profile_export_refuses_installation_key_before_file_creation(self):
        with tempfile.TemporaryDirectory(prefix="ott-installation-export-") as temporary:
            path = Path(temporary) / "profile.json"
            for location in ("envelope", "profile", "source"):
                response = {"provider": "offline", "number": 1, "profile": local_profiles.document()}
                destination = (response if location == "envelope" else response["profile"]
                               if location == "profile" else response["profile"]["sources"][0])
                destination[SETTING] = SERVICE
                with self.subTest(location=location):
                    code, out, err, client = local_profiles.OfflineProfileTest.run_cli(
                        self, ["profile", "export", "1", str(path)], [local_profiles.caps(), response], True)
                    self.assertEqual((code, out, path.exists()), (1, "", False))
                    self.assertEqual(client.call.call_count, 2)
                    self.assertEqual(client.call.call_args, mock.call("dev_tv", "profile_export", {"number": 1}))
                    self.assert_private(err)

    def test_explicit_private_file_set_and_empty_disable_remain_redacted(self):
        with tempfile.TemporaryDirectory(prefix="ott-installation-setting-") as temporary:
            path = Path(temporary) / "service.json"
            for value in (SERVICE, ""):
                # The generic string contract preserves authored JSON text; it
                # does not interpret or normalize the installation credential.
                path.write_text(value, encoding="utf-8")
                for json_output in (False, True):
                    get, saved = sensitive_responses(configured=bool(value))
                    words = (["-j"] if json_output else []) + [
                        "f", "setting", SETTING, "--value-file", str(path)]
                    with self.subTest(empty=not value, json_output=json_output):
                        code, out, err, client = settings_cli.PlayerSettingsCliTests.run_cli(
                            self, words, [settings_cli.caps(), get, saved])
                        self.assertEqual((code, err, client.call.call_count), (0, "", 3))
                        self.assertEqual(client.call.call_args, mock.call("device-f", "player_settings", {
                            "op": "set", "runtime": "page-123", "id": SETTING,
                            "context": get["context"]["token"], "revision": get["setting"]["revision"],
                            "value": value,
                        }))
                        self.assert_private(out, err)
                        if json_output:
                            self.assertEqual(json.loads(out), saved)
                        else:
                            self.assertIn(SETTING + ": saved [redacted]", out)

    def test_sensitive_receipt_with_exposed_value_is_rejected_without_replay(self):
        with tempfile.TemporaryDirectory(prefix="ott-installation-invalid-") as temporary:
            path = Path(temporary) / "service.json"
            path.write_text(SERVICE, encoding="utf-8")
            for field in ("value", "persisted_value", "effective_value"):
                get, saved = sensitive_responses()
                saved["setting"][field] = SERVICE
                with self.subTest(field=field):
                    code, out, err, client = settings_cli.PlayerSettingsCliTests.run_cli(
                        self, ["-j", "f", "setting", SETTING, "--value-file", str(path)],
                        [settings_cli.caps(), get, saved])
                    self.assertEqual((code, out, client.call.call_count), (1, "", 3))
                    self.assert_private(err)

    def test_group_binding_writes_refuse_before_file_read_or_any_rpc(self):
        for target in ("f,g", "both"):
            for tail in ([SERVICE], [""], ["--value-file", "must-not-read-private-service.json"]):
                with self.subTest(target=target, file=tail[0] == "--value-file", empty=tail == [""]), \
                        mock.patch.object(Path, "open", side_effect=AssertionError("private file was opened")):
                    code, out, err, client = self.group_cli([target, "setting", SETTING, *tail])
                    self.assertEqual((code, out, client.calls), (1, "", []))
                    self.assertIn("require one player", err)
                    self.assert_private(err)

    def test_group_binding_reads_remain_allowed_and_redacted(self):
        for target in ("f,g", "both"):
            with self.subTest(target=target):
                code, out, err, client = self.group_cli(["-j", target, "setting", SETTING])
                self.assertEqual((code, err), (0, ""))
                result = json.loads(out)
                self.assertEqual((result["succeeded"], result["failed"]), (2, 0))
                for row in result["results"]:
                    setting = row["data"]["setting"]
                    self.assertEqual(setting, sensitive_responses()[0]["setting"])
                self.assertEqual(len(client.calls), 4)
                self.assertEqual({device for device, _, _ in client.calls}, {"device-f", "device-g"})
                self.assertTrue(all(action == "capabilities" or params["op"] == "get"
                                    for _, action, params in client.calls))
                self.assert_private(out, err)

    def test_other_group_setting_writes_are_not_blocked(self):
        code, out, err, client = self.group_cli(["-j", "both", "setting", "bufSize", "3"])
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual((result["succeeded"], result["failed"]), (2, 0))
        writes = [(device, params) for device, action, params in client.calls
                  if action == "player_settings" and params["op"] == "set"]
        self.assertEqual({device for device, _ in writes}, {"device-f", "device-g"})
        self.assertEqual(len(writes), 2)
        self.assertTrue(all(params["id"] == "bufSize" and params["value"] == 3 for _, params in writes))


if __name__ == "__main__":
    unittest.main()
