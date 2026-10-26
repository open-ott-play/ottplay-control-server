import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ott_player_settings_cli", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
spec = importlib.util.spec_from_file_location("settings_test_module", ROOT / "cli/player_settings.py")
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)
FIXTURE = json.loads((ROOT / "contracts/player-settings-v1.json").read_text())
SECRET = "private-fixture-value-never-printed"


def caps():
    return {"version": 1, "player": {"version": "1.1.60", "platform": "browser", "runtime": "page-123"},
            "lifecycle": [], "input": [], "playback": [], "player_settings": copy.deepcopy(FIXTURE["capability"])}


def result(op):
    return copy.deepcopy(FIXTURE["responses"][op])


class PlayerSettingsCliTests(unittest.TestCase):
    def run_cli(self, words, responses):
        client = ott.Client.__new__(ott.Client)
        client.config = {"players": {"f": "device-f"}}
        client.credentials = {"devices": [{"id": "device-f"}]}
        client.timeout = 45
        client.call = mock.Mock(side_effect=responses)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=client.config), \
                mock.patch.object(ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client

    def test_read_and_write_use_get_descriptor_revision_and_one_shared_source(self):
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"], [caps(), result("get"), result("set")])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("saved 3 seconds", out)
        self.assertIn("next stream", out)
        self.assertEqual(client.call.call_args_list, [mock.call("device-f", "capabilities", {}),
            mock.call("device-f", "player_settings", FIXTURE["requests"]["get"]),
            mock.call("device-f", "player_settings", FIXTURE["requests"]["set"])])
        code, out, err, client = self.run_cli(["-j", "f", "setting", "bufSize"], [caps(), result("get")])
        self.assertEqual((code, err, json.loads(out)), (0, "", result("get")))
        self.assertEqual(client.call.call_count, 2)

    def test_engine_alias_is_supplied_by_canonical_schema(self):
        get, write = result("get"), result("set")
        descriptor = get["descriptor"]
        descriptor.update(id="players", scope="provider", constraints={"minimum": 0, "maximum": 3},
                          choices=[{"value": 1, "label": "hls.js", "aliases": ["hls.js", "hls"]}])
        descriptor.pop("unit")
        get["setting"]["id"] = write["setting"]["id"] = "players"
        write["setting"]["value"] = write["setting"]["persisted_value"] = 1
        code, out, err, client = self.run_cli(["f", "setting", "players", "HLS.JS"], [caps(), get, write])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(client.call.call_args.args[2]["value"], 1)
        self.assertIn("provider m3u / profile 1", out)

    def test_bad_values_fail_without_set_and_dont_print_secrets(self):
        for value in ["-1", "3601", "3.5", "true", SECRET]:
            code, out, err, client = self.run_cli(["f", "setting", "bufSize", value], [caps(), result("get")])
            self.assertEqual((code, out, client.call.call_count), (1, "", 2))
            self.assertNotIn(SECRET, err)

    def test_old_player_and_restricted_setting_do_not_write(self):
        old = caps(); old.pop("player_settings")
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"], [old])
        self.assertEqual((code, out, client.call.call_count), (1, "", 1))
        get = result("get"); get["descriptor"]["access"] = {"read": True, "write": False, "reason": "policy_restricted"}
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"], [caps(), get])
        self.assertEqual((code, out, client.call.call_count), (1, "", 2))

    def test_schema_paging_requires_same_source_and_unique_ids(self):
        page = {"version":1,"op":"schema","runtime":"page-123","context":copy.deepcopy(FIXTURE["context"]),
                "settings":[copy.deepcopy(FIXTURE["descriptor"])],"next_cursor":None}
        code, out, err, client = self.run_cli(["f", "settings"], [caps(), page])
        self.assertEqual((code, err), (0, "")); self.assertIn("bufSize", out)
        first = copy.deepcopy(page); first["next_cursor"] = "page-2"
        second = copy.deepcopy(page); second["context"]["token"] = "different-source"
        code, out, err, client = self.run_cli(["f", "settings", "schema"], [caps(), first, second])
        self.assertEqual((code, out, client.call.call_count), (1, "", 3))
        self.assertIn("source changed", err)

    def test_app_preference_does_not_require_mounted_provider(self):
        get, write = result("get"), result("set")
        for data in [get, write]: data["context"].update(provider=None, profile=None)
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"], [caps(), get, write])
        self.assertEqual((code, err), (0, ""))

    def test_unconfirmed_or_cross_runtime_results_never_report_saved(self):
        for key, value in [("persisted", False), ("runtime", "other-page"), ("version", True)]:
            write = result("set"); write[key] = value
            code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"], [caps(), result("get"), write])
            self.assertEqual((code, out, client.call.call_count), (1, "", 3))
        write = result("set"); write["setting"]["persisted_value"] = 4
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"], [caps(), result("get"), write])
        self.assertEqual((code, out, client.call.call_count), (1, "", 3))

    def test_effect_failure_reports_saved_but_returns_nonzero(self):
        write = result("set"); write["application"] = {"state":"failed","reason":"effect_failed"}
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"], [caps(), result("get"), write])
        self.assertEqual((code, err, client.call.call_count), (1, "", 3))
        self.assertIn("saved 3", out); self.assertIn("failed after saving", out)

    def test_rejection_uses_local_reason_and_never_replays(self):
        error = result("error"); error["error"] = SECRET
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", "3"],
            [caps(), result("get"), ott.PlayerRejected(SECRET, error)])
        self.assertEqual((code, out, client.call.call_count), (1, "", 3))
        self.assertIn("concurrently", err); self.assertNotIn(SECRET, err)

    def test_secret_get_and_set_values_are_redacted(self):
        get, write = result("get"), result("set")
        get["descriptor"].update(type="string", sensitive=True, constraints={"max_utf8_bytes":4096})
        get["descriptor"].pop("default_value")
        for data in [get, write]:
            data["setting"] = {"id":"bufSize","revision":"revision-1","storage":"stored","redacted":True,"configured":True}
        code, out, err, client = self.run_cli(["f", "setting", "bufSize", SECRET], [caps(), get, write])
        self.assertEqual((code, err), (0, "")); self.assertNotIn(SECRET, out)
        get["setting"]["value"] = SECRET
        code, out, err, client = self.run_cli(["f", "setting", "bufSize"], [caps(), get])
        self.assertEqual((code, out), (1, "")); self.assertNotIn(SECRET, err)

    def test_malformed_nested_metadata_is_safe(self):
        for value in [None, [], {"read":True,"write":True,"reason":[]}]:
            get = result("get"); get["descriptor"]["access"] = value
            code, out, err, client = self.run_cli(["f", "setting", "bufSize"], [caps(), get])
            self.assertEqual((code, out), (1, ""))

    def test_whole_operation_timeout_never_refreshes_for_set(self):
        client = mock.Mock(timeout=45)
        client.call.side_effect = [caps(), result("get")]
        with mock.patch.object(settings.time, "monotonic", side_effect=[0, 0, 1, 44, 46]):
            with self.assertRaisesRegex(ott.Error, "exceeded --timeout"):
                settings.run(ott, client, "device-f", ["setting", "bufSize", "3"], 45, False)
        self.assertEqual(client.call.call_count, 2)
        self.assertEqual(client.timeout, 45)

    def test_shared_descriptor_and_application_negative_corpus(self):
        for override in FIXTURE['invalid_descriptor_overrides']:
            descriptor = copy.deepcopy(FIXTURE['descriptor']); descriptor.update(override)
            self.assertFalse(settings.descriptor_valid(descriptor), override)
        for application in FIXTURE['invalid_applications']:
            write = result('set'); write['application'] = application
            with self.assertRaises(ott.Error):
                settings.metadata(ott,write,FIXTURE['requests']['set'],FIXTURE['descriptor'])

    def test_registered_provider_context_ids_use_shared_corpus(self):
        for valid, key in [(True, 'valid_provider_ids'), (False, 'invalid_provider_ids')]:
            for provider in FIXTURE[key]:
                response = result('get')
                response['context']['provider'] = provider
                if valid:
                    self.assertEqual(settings.metadata(ott, response, FIXTURE['requests']['get']), response)
                else:
                    with self.assertRaises(ott.Error, msg=repr(provider)):
                        settings.metadata(ott, response, FIXTURE['requests']['get'])

    def test_nonsecret_redaction_and_missing_stored_values_are_invalid(self):
        for operation in ['get','set']:
            response = result(operation)
            response['setting'] = {'id':'bufSize','revision':'r','storage':'stored','redacted':True}
            with self.assertRaises(ott.Error):
                settings.metadata(ott,response,FIXTURE['requests'][operation],FIXTURE['descriptor'])
        for key in ['value','persisted_value']:
            response = result('get'); del response['setting'][key]
            with self.assertRaises(ott.Error):
                settings.metadata(ott,response,FIXTURE['requests']['get'])

    def test_value_file_read_is_bounded_before_any_remote_call(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.read.return_value = b'x'*16385
        client = mock.Mock(timeout=45)
        with mock.patch.object(settings.Path,'open',return_value=stream):
            with self.assertRaisesRegex(ott.Error,'bounded UTF-8'):
                settings.run(ott,client,'device-f',['setting','bufSize','--value-file','private'],45,False)
        stream.read.assert_called_once_with(16385)
        client.call.assert_not_called()

    def test_array_request_budget_prevents_oversized_set(self):
        get = result('get')
        get['descriptor'].update(type='string_array',constraints={'max_items':64,'max_utf8_bytes':4096},default_value=[])
        get['setting'].update(value=[],persisted_value=[])
        value = json.dumps(['x'*4096]*4)
        code,out,err,client = self.run_cli(['f','setting','bufSize',value],[caps(),get])
        self.assertEqual((code,out,client.call.call_count),(1,'',2))
        self.assertIn('16 KiB',err)


if __name__ == "__main__": unittest.main()
