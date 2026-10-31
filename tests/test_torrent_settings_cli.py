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
spec = importlib.util.spec_from_file_location("ott_torrent_settings_cli", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
spec = importlib.util.spec_from_file_location("torrent_settings_module", ROOT / "cli/player_settings.py")
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)
FIXTURE = json.loads((ROOT / "contracts/player-settings-v1.json").read_text())
SECRET = "private-addon-credential-never-print"
MANIFEST = "https://addon.example/" + SECRET + "/manifest.json"


def caps():
    return {"version": 1, "player": {"version": "1.1.62", "platform": "tauri", "runtime": "page-123"},
            "lifecycle": [], "input": [], "playback": [], "player_settings": copy.deepcopy(FIXTURE["capability"])}


def result(op, identifier="torrentStreamAddons"):
    data = copy.deepcopy(FIXTURE["responses"][op])
    data["context"].update(provider=None, profile=None)
    data["setting"] = {"id": identifier, "revision": "revision-1", "storage": "stored",
                       "redacted": True, "configured": True}
    if op == "get":
        data["descriptor"] = {"id": identifier, "type": "string", "scope": "application", "sensitive": True,
                              "access": {"read": False, "write": True},
                              "constraints": {"max_utf8_bytes": 4096}, "apply": "immediate"}
    else:
        data["application"] = {"state": "completed"}
    return data


class TorrentSettingsCliTests(unittest.TestCase):
    def run_cli(self, words, responses):
        client = ott.Client.__new__(ott.Client)
        client.config = {"players": {"t1": "device-1", "t2": "device-2"}}
        client.credentials = {"devices": [{"id": "device-1"}, {"id": "device-2"}]}
        client.timeout = 45
        client.call = mock.Mock(side_effect=responses)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=client.config), \
                mock.patch.object(ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        self.assertEqual(client.timeout, 45)
        self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue(), client

    def assert_preflight_rejected(self, value):
        code, out, err, client = self.run_cli(["t1", "setting", "torrentStreamAddons", value], [])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("JSON array", err)
        client.call.assert_not_called()

    def test_pretty_file_normalizes_once_and_saves_scalar_with_redacted_output(self):
        values = [" \t" + MANIFEST + "///\n", MANIFEST,
                  "https://other.example/config%20space/%2F/manifest.json"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            path.write_text(json.dumps(values, indent=2) + "\n")
            code, out, err, client = self.run_cli(
                ["t1", "setting", "torrentStreamAddons", "--value-file", str(path)],
                [caps(), result("get"), result("set")])
        self.assertEqual((code, err, client.call.call_count), (0, "", 3))
        self.assertIn("saved [redacted]", out)
        params = client.call.call_args.args[2]
        self.assertEqual(params, {"op": "set", "runtime": "page-123", "id": "torrentStreamAddons",
                                  "context": "context-1", "revision": "revision-1",
                                  "value": json.dumps([MANIFEST, values[2]], separators=(",", ":"))})
        self.assertIsInstance(params["value"], str)

    def test_inline_empty_array_clears_without_changing_wire_type(self):
        code, _, err, client = self.run_cli(["-j", "t1", "setting", "torrentStreamAddons", " [ ] "],
                                           [caps(), result("get"), result("set")])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(client.call.call_args.args[2]["value"], "[]")

    def test_eight_items_and_exact_deduplication_preserve_spelling_order_and_unicode(self):
        values = [f"https://addon.example/{number}/manifest.json" for number in range(8)]
        self.assertEqual(json.loads(settings.torrent_stream_addons_value(ott, json.dumps(values))), values)
        variants = ["HTTPS://ADDON.example/Ключ/manifest.json", "https://addon.example/Ключ/manifest.json"]
        normalized = settings.torrent_stream_addons_value(ott, json.dumps(variants + variants))
        self.assertEqual(normalized, json.dumps(variants, ensure_ascii=False, separators=(",", ":")))
        self.assert_preflight_rejected(json.dumps([MANIFEST] * 9))

    def test_compact_utf8_limit_and_pretty_source_budget(self):
        prefix, suffix = "https://addon.example/", "/manifest.json"
        def value(size):
            return json.dumps([prefix + "x" * (size - len(prefix + suffix) - 4) + suffix])
        exact = value(4096)
        self.assertEqual(len(exact.encode()), 4096)
        self.assertEqual(settings.torrent_stream_addons_value(ott, exact), exact)
        pretty = "\n" * 5000 + exact + "\n" * 5000
        self.assertEqual(settings.torrent_stream_addons_value(ott, pretty), exact)
        self.assert_preflight_rejected(value(4097))
        unicode_value = json.dumps([prefix + "é" * 2030 + suffix], ensure_ascii=False)
        self.assertLess(len(unicode_value), 4096)
        self.assertGreater(len(unicode_value.encode()), 4096)
        self.assert_preflight_rejected(unicode_value)

    def test_invalid_json_shapes_and_lone_surrogates_fail_before_rpc(self):
        for value in ("", "{", "{}", "null", "true", "1", '"string"', "[null]", "[1]", "[true]", "[[]]",
                      "[{}]", '["https://addon.example/\\ud800/manifest.json"]',
                      "[" * 2000 + "]" * 2000):
            with self.subTest(value=value[:30]):
                self.assert_preflight_rejected(value)

    def test_invalid_urls_fail_before_rpc_without_echoing_values(self):
        paths = [
            "http://addon.example/manifest.json", "https:///manifest.json", "https://addon.example/not-manifest",
            "https://addon.example/Manifest.json", "https://addon.example/manifest.json?",
            "https://addon.example/manifest.json#", "https://@addon.example/manifest.json",
            "https://user:password@addon.example/manifest.json", "https://addon.example:65536/manifest.json",
            "https://addon.example:no/manifest.json", "https://[invalid]/manifest.json",
            "https://addon.example/space here/manifest.json", "https://addon.example/\\folder/manifest.json",
            "https://addon.example/\x00/manifest.json", "https://addon.example/\x7f/manifest.json",
            "https://addon.example/quote'/manifest.json", 'https://addon.example/quote"/manifest.json',
            "https://addon.example/<private>/manifest.json", "https://addon.example/%zz/manifest.json",
            "https://addon.example/%/manifest.json", "https://addon.example/%ff/manifest.json",
            "https://addon.example/%C0%AF/manifest.json", "https://addon.example/%00/manifest.json",
            "https://addon.example/%7f/manifest.json", "https://addon.example/%5C/manifest.json",
            "https://exa^mple.org/manifest.json", "https://exa|mple.org/manifest.json",
            "https://%zz/manifest.json", "https://%00/manifest.json", "https://a%2fb/manifest.json",
            "https://%ff/manifest.json", "https://a%5cb/manifest.json", "https://a%3ab/manifest.json",
            "https://a%40b/manifest.json", "https://a%25b/manifest.json", "https://%7f/manifest.json",
            "https://[::1%25eth0]/manifest.json", "https://[v1.example]/manifest.json",
            "https://999.999.999.999/manifest.json", "https://1.2.3.256/manifest.json",
            "https://1.2.3.4.5/manifest.json",
            MANIFEST + "?private=" + SECRET,
        ]
        for manifest in paths:
            with self.subTest(manifest=manifest):
                self.assert_preflight_rejected(json.dumps([manifest]))

    def test_ipv6_private_hosts_valid_ports_and_encoded_paths_are_not_rewritten(self):
        values = ["https://[::1]:65535/private/manifest.json", "https://localhost:443/manifest.json",
                  "https://10.0.0.1/private%20value/%2F/%E2%82%AC/manifest.json",
                  "https://медиа.example/manifest.json", "https://%D0%BC.example/manifest.json",
                  "https://127.1/manifest.json", "https://0377.0.0.1/manifest.json"]
        self.assertEqual(json.loads(settings.torrent_stream_addons_value(ott, json.dumps(values))), values)

    def test_http_accepts_only_literal_loopback_authorities_and_valid_ports(self):
        values = ["http://localhost/manifest.json", "http://127.0.0.1:8080/addon/manifest.json",
                  "http://[::1]:65535/addon/manifest.json", "HTTP://LOCALHOST:00080/manifest.json",
                  "http://localhost:0/manifest.json"]
        self.assertEqual(json.loads(settings.torrent_stream_addons_value(ott, json.dumps(values))), values)
        rejected = ["example.org", "10.0.0.1", "localhost.example.org", "addon.localhost", "localhost.",
                    "127.0.0.2", "127.1", "127.000.000.001", "2130706433", "0x7f000001",
                    "%6cocalhost", "local%68ost", "localhoſt", "ｌｏｃａｌｈｏｓｔ",
                    "[0:0:0:0:0:0:0:1]", "[::ffff:127.0.0.1]",
                    "@localhost", "user@localhost", "localhost:65536", "localhost:000080",
                    "localhost:", "localhost:-1"]
        for authority in rejected:
            with self.subTest(authority=authority):
                self.assert_preflight_rejected(json.dumps(["http://" + authority + "/manifest.json"]))

    def test_mixed_loopback_http_and_https_file_preserves_conditional_scalar_save(self):
        values = ["http://127.0.0.1:8080/" + SECRET + "/manifest.json", MANIFEST]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            path.write_text(json.dumps(values, indent=2))
            code, _, err, client = self.run_cli(
                ["-j", "t1", "setting", "torrentStreamAddons", "--value-file", str(path)],
                [caps(), result("get"), result("set")])
        self.assertEqual((code, err, client.call.call_count), (0, "", 3))
        self.assertEqual(client.call.call_args.args[2]["value"], json.dumps(values, separators=(",", ":")))

    def test_metadata_loopback_base_remains_an_opaque_typed_setting(self):
        value = "http://[::1]:8080/" + SECRET
        code, _, err, client = self.run_cli(["t1", "setting", "torrentCatalogEndpoint", value],
            [caps(), result("get", "torrentCatalogEndpoint"), result("set", "torrentCatalogEndpoint")])
        self.assertEqual((code, err, client.call.call_count), (0, "", 3))
        self.assertEqual(client.call.call_args.args[2]["value"], value)

    def test_bad_files_are_rejected_without_rpc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            for payload in (b"\xff", b" " * 16385, b"not JSON", b'["http://addon.example/manifest.json"]'):
                path.write_bytes(payload)
                code, out, _, client = self.run_cli(
                    ["t1", "setting", "torrentStreamAddons", "--value-file", str(path)], [])
                self.assertEqual((code, out), (1, ""))
                client.call.assert_not_called()
            path.unlink()
            code, _, _, client = self.run_cli(
                ["t1", "setting", "torrentStreamAddons", "--value-file", str(path)], [])
            self.assertEqual(code, 1)
            client.call.assert_not_called()

    def test_missing_value_file_argument_is_not_saved_as_literal(self):
        for identifier in ("torrentStreamAddons", "commandServerToken"):
            code, out, err, client = self.run_cli(["t1", "setting", identifier, "--value-file"], [])
            self.assertEqual((code, out), (1, ""))
            self.assertIn("--value-file FILE", err)
            client.call.assert_not_called()

    def test_plain_secret_strings_are_not_trimmed_or_treated_as_json(self):
        raw = "  " + SECRET + "  "
        code, _, err, client = self.run_cli(["t1", "setting", "commandServerToken", raw],
                                           [caps(), result("get", "commandServerToken"),
                                            result("set", "commandServerToken")])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(client.call.call_args.args[2]["value"], raw)

    def test_generic_string_file_still_preserves_interior_newlines_and_fails_type_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.txt"
            path.write_text(SECRET + "\nsecond line\n")
            code, out, _, client = self.run_cli(
                ["t1", "setting", "commandServerToken", "--value-file", str(path)],
                [caps(), result("get", "commandServerToken")])
        self.assertEqual((code, out, client.call.call_count), (1, "", 2))

    def test_get_remains_redacted_and_does_not_change_any_setting(self):
        code, out, err, client = self.run_cli(["-j", "t1", "setting", "torrentStreamAddons"],
                                           [caps(), result("get")])
        self.assertEqual((code, err, client.call.call_count), (0, "", 2))
        self.assertEqual(json.loads(out)["setting"], result("get")["setting"])

    def test_rejected_conditional_save_is_not_retried_and_error_is_redacted(self):
        rejection = copy.deepcopy(FIXTURE["responses"]["error"])
        rejection["error"] = SECRET
        code, out, err, client = self.run_cli(["t1", "setting", "torrentStreamAddons", json.dumps([MANIFEST])],
            [caps(), result("get"), ott.PlayerRejected(SECRET, rejection)])
        self.assertEqual((code, out, client.call.call_count), (1, "", 3))
        self.assertIn("concurrently", err)

    def test_group_uses_the_same_private_input_file_and_redacts_each_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            path.write_text(json.dumps([MANIFEST, "http://localhost:8080/manifest.json"], indent=2))
            code, out, err, client = self.run_cli(
                ["-j", "t1,t2", "setting", "torrentStreamAddons", "--value-file", str(path)],
                [caps(), result("get"), result("set"), caps(), result("get"), result("set")])
        self.assertEqual((code, err, client.call.call_count), (0, "", 6))
        self.assertEqual(json.loads(out)["succeeded"], 2)
        writes = [call for call in client.call.call_args_list if call.args[2].get("op") == "set"]
        self.assertEqual([call.args[0] for call in writes], ["device-1", "device-2"])
        self.assertEqual(writes[0].args[2]["value"], writes[1].args[2]["value"])


if __name__ == "__main__":
    unittest.main()
