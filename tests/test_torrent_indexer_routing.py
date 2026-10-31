"""Local indexer commands keep normal CLI routing and offline validation."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ott_indexer_routing", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class IndexerRoutingTest(unittest.TestCase):
    def invoke(self, words, config=None, client=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=config or {}) as read, \
                mock.patch.object(ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), read.call_count

    def test_help_does_not_load_credentials(self):
        code, out, err, reads = self.invoke(["t1", "torrent", "indexer", "--help"])
        self.assertEqual((code, err, reads), (0, "", 0))
        for word in ("configure", "caps", "search", "prepare", "--api-key-file", "--api-key-env"):
            self.assertIn(word, out)

    def test_invalid_arguments_stop_before_credentials(self):
        for words in (["remove", "Bad-ID"], ["prepare", "home", "not-a-ref"],
                      ["search", "home", "movie", "--limit", "101"],
                      ["configure", "home", "--api-key", "PRIVATE_KEY"]):
            with self.subTest(words=words):
                code, out, err, reads = self.invoke(["t1,t2", "torrent", "indexer", *words])
                self.assertEqual((code, out, reads), (1, "", 0))
                self.assertNotIn("PRIVATE_KEY", err)

    def test_indexer_has_normal_timeout_ceiling(self):
        code, out, err, reads = self.invoke(["-t", "301", "t1", "torrent", "indexer", "list"])
        self.assertEqual((code, out, reads), (1, "", 0))
        self.assertIn("--timeout", err)

    def test_marker_is_versioned_and_exact(self):
        base = {"version": 1, "player": {"version": "test", "platform": "tauri", "runtime": "runtime"},
                "lifecycle": [], "input": [], "playback": []}
        self.assertNotIn("torrent_indexer", ott.capabilities_metadata(base))
        self.assertEqual(ott.capabilities_metadata(dict(base, torrent_indexer={"version": 1}))["torrent_indexer"], {"version": 1})
        for value in ({"version": True}, {"version": 2}, {"version": 1, "endpoint": "PRIVATE"}, None):
            with self.subTest(value=value), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(base, torrent_indexer=value))

    def test_group_dispatches_indexer_once_per_target(self):
        config = {"players": {"t1": "dev1", "t2": "dev2"}}
        client = SimpleNamespace(config=config, server="https://controller.example", timeout=45, on_receipt=None,
                                 credentials={"devices": [{"id": value} for value in config["players"].values()]},
                                 device=lambda name: config["players"][name])
        module = ott.torrent_indexer_module()
        calls = []

        def run(api, current, device, words, timeout, as_json, path):
            calls.append((device, words, timeout, as_json))
            print(json.dumps({"sources": []}))
            return 0

        module.run = run
        with mock.patch.object(ott, "torrent_indexer_module", return_value=module):
            code, out, err, _ = self.invoke(["-j", "-t", "45", "t1,t2", "torrent", "indexer", "list"], config, client)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["succeeded"], 2)
        self.assertEqual(calls, [("dev1", ["list"], 45, True), ("dev2", ["list"], 45, True)])

    def test_only_exact_local_source_uri_is_accepted(self):
        doc = ott.torrent_module().doc
        source = "ottplay-local-torrent:home/" + "a" * 32
        self.assertEqual(doc.source(source), source)
        for value in (source + "?key=x", source + "#fragment", source + "/extra", source.upper(),
                      source.replace(":home/", "://home/"), source.replace(":home/", ":../"),
                      source[:-1], source + "a", source.replace("home", "Home"), source + "\n"):
            with self.subTest(value=value), self.assertRaises(doc.DocumentError):
                doc.source(value)

    def test_operation_reports_accept_torrent_actions_and_drop_private_fields(self):
        spec = importlib.util.spec_from_file_location("indexer_workbench", ROOT / "cli/workbench.py")
        workbench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workbench)
        for action in ("torrent_catalog", "torrent_indexer"):
            raw = {"version": 1, "runtime": "runtime", "section": "operation", "data": {
                "operation_id": "a" * 32, "state": "invoked", "action": action,
                "evidence": {"kind": "handler_completed", "generation": None, "position": None},
                "endpoint": "http://private.invalid", "apiKey": "PRIVATE_KEY"}}
            result = workbench.operation_metadata(raw, "runtime", "a" * 32)
            self.assertEqual(result["action"], action)
            self.assertNotIn("endpoint", result)
            self.assertNotIn("PRIVATE_KEY", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
