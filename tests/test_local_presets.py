"""Local folder configuration participates in the existing preset transaction."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("preset_fixtures", Path(__file__).with_name("test_presets_cli.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
ott = fixtures.ott


def source(name="Movies", path="/Volumes/data/Movies", ident="movies", available=True):
    row = {"id": ident, "name": name, "kind": "folder", "available": available}
    if path is not None:
        row["path"] = path
    return row


class LocalPresetTests(unittest.TestCase):
    def setUp(self):
        self.player = fixtures.FakePlayer()
        self.sources = []
        self.aliases = {}
        self.native = True
        self.local_calls = []
        self.connect_error = None
        self.capability_error = None
        self.patches = [
            mock.patch.object(ott, "local_media_capabilities", side_effect=self.capabilities),
            mock.patch.object(ott, "local_media_command", side_effect=self.command),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def capabilities(self, *args):
        if self.capability_error:
            raise self.capability_error
        return {"local_media": {"absolute_paths": self.native, "operations": ["sources", "connect"]}}

    def command(self, client, device, params, timeout, session=None):
        self.local_calls.append(copy.deepcopy(params))
        if params["op"] == "sources":
            rows = self.sources
            if "path" in params:
                path = self.aliases.get(params["path"], params["path"]).rstrip("/\\")
                rows = [row for row in rows if row.get("path", "").rstrip("/\\") == path]
            return {"sources": copy.deepcopy(rows)}
        self.assertEqual(params["op"], "connect")
        # A failed connection must precede all provider-setting mutations.
        self.assertEqual(self.player.saved, [])
        if self.connect_error:
            raise self.connect_error
        selector = params["source"]
        row = source(name=Path(selector).name, path=selector)
        self.sources.append(row)
        return {"accepted": True, "playback_confirmed": False, "source": row}

    def normalized(self, local=None):
        config = fixtures.config()
        config["presets"]["local"]["local"] = local or {"sources": [{"name": "Movies", "path": "/Volumes/data/Movies"}]}
        return ott.validate_preset(config, "local")[1]

    def load(self, preset=None):
        return ott.load_preset(self.player, "dev_tv", "local", preset or self.normalized())

    def test_native_folder_connected_before_other_settings_and_verified_without_playback(self):
        self.sources = [source("Other", "/Volumes/data/Other", "other")]
        result = self.load()
        self.assertEqual(result["status"], "loaded")
        self.assertEqual(result["completed"][0], "connect_local_1")
        self.assertEqual(result["completed"][-1], "verify_local")
        self.assertEqual(self.player.provider, "m3u")
        self.assertEqual([c["op"] for c in self.local_calls].count("connect"), 1)
        self.assertEqual({r["name"] for r in self.sources}, {"Other", "Movies"})
        self.assertFalse(any(action in ("play", "play_catalog") for _, action, _ in self.player.calls))

    def test_already_connected_path_is_not_reconnected(self):
        self.sources = [source()]
        result = self.load()
        self.assertEqual(result["status"], "loaded")
        self.assertFalse(any(c["op"] == "connect" for c in self.local_calls))

    def test_browser_reuses_granted_name_without_attempting_native_path(self):
        self.native = False
        self.sources = [source(path=None)]
        self.assertEqual(self.load()["status"], "loaded")
        self.assertFalse(any(c.get("include_paths") for c in self.local_calls))
        self.assertFalse(any(c["op"] == "connect" for c in self.local_calls))

    def test_target_canonical_path_alias_is_verified_without_reconnecting(self):
        self.sources = [source(path="/private/var/media/Movies")]
        self.aliases["/var/media/Movies"] = "/private/var/media/Movies"
        preset = self.normalized({"sources": [{"name": "Movies", "path": "/var/media/Movies"}]})
        self.assertEqual(self.load(preset)["status"], "loaded")
        self.assertFalse(any(c["op"] == "connect" for c in self.local_calls))

    def test_new_connection_name_mismatch_stops_before_other_settings(self):
        preset = self.normalized({"sources": [{"name": "Films", "path": "/Volumes/data/Movies"}]})
        result = self.load(preset)
        self.assertEqual((result["status"], result["stage"]), ("failed", "connect_local_1"))
        self.assertEqual(self.player.calls, [])

    def test_missing_browser_grant_stops_before_any_provider_writes(self):
        self.native = False
        result = self.load()
        self.assertEqual((result["status"], result["stage"]), ("failed", "preflight_local"))
        self.assertEqual(self.player.calls, [])

    def test_old_player_stops_before_any_provider_writes(self):
        self.capability_error = ott.Error("unsupported")
        self.assertEqual(self.load()["stage"], "preflight_local")
        self.assertEqual(self.player.calls, [])

    def test_duplicate_browser_names_do_not_pick_arbitrary_folder(self):
        self.native = False
        self.sources = [source(path=None), source(path=None, ident="other-movies")]
        self.assertEqual(self.load()["stage"], "preflight_local")
        self.assertEqual(self.player.calls, [])

    def test_same_native_name_does_not_replace_path_identity(self):
        self.sources = [source(path="/wrong/Movies", ident="wrong")]
        result = self.load()
        self.assertEqual(result["status"], "loaded")
        self.assertEqual([c["source"] for c in self.local_calls if c["op"] == "connect"], ["/Volumes/data/Movies"])

    def test_timeout_is_not_repeated_and_no_other_settings_are_written(self):
        self.connect_error = ott.Error("outcome unknown")
        result = self.load()
        self.assertEqual((result["status"], result["stage"]), ("failed", "connect_local_1"))
        self.assertEqual(sum(c["op"] == "connect" for c in self.local_calls), 1)
        self.assertEqual(self.player.calls, [])

    def test_unconfirmed_connection_stops_transaction(self):
        with mock.patch.object(ott, "local_preset_connected", return_value=False):
            result = self.load()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.player.calls, [])

    def test_schema_rejects_ambiguous_or_non_absolute_configuration(self):
        invalid = [
            {}, {"sources": []}, {"sources": [{"name": "Movies", "path": "./Movies"}]},
            {"sources": [{"name": "Movies", "path": "/Volumes/../Movies"}]},
            {"sources": [{"name": "Movies"}, {"name": "Movies"}]},
            {"sources": [{"name": "Movies", "path": "/media"}, {"name": "Other", "path": "/media/"}]},
            {"sources": [{"name": "Movies", "token": "unexpected"}]},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ott.Error):
                ott.validate_local_preset(value)

    def test_schema_preserves_private_paths_and_accepts_name_only(self):
        value = {"sources": [{"name": "Movies", "path": "/Volumes/data/Movies/"}, {"name": "Kids"}]}
        self.assertEqual(ott.validate_local_preset(value), value)

    def test_colon_named_folder_can_be_delivered_in_a_preset(self):
        value = {"sources": [{"name": "Kids: Cartoons", "path": "/Volumes/data/Kids: Cartoons"}]}
        self.assertEqual(ott.validate_local_preset(value), value)
        result = self.load(self.normalized(value))
        self.assertEqual(result["status"], "loaded")
        self.assertEqual(self.sources[0]["name"], "Kids: Cartoons")

    def test_preset_folder_names_retain_basename_and_byte_limits(self):
        for name in (".", "..", "A/B", "A\\B", "A\nB", "A\x85B", "Я" * 257):
            with self.subTest(name=name), self.assertRaises(ott.Error):
                ott.validate_local_preset({"sources": [{"name": name}]})

    def test_connect_checkpoint_reconciles_lost_reply_without_replaying(self):
        module_spec = importlib.util.spec_from_file_location("local_preset_session", Path(__file__).resolve().parents[1] / "cli/preset_session.py")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        player = self.player
        player.server = "https://controller.example"
        operations = {}
        player.api = lambda path: (200, operations.get(path.split("&key=")[-1], {"enabled": True, "command_ttl_seconds": 60}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            current = module.Session(ott, player, "dev_tv", "local", self.normalized(), path, False)
            params = {"op": "connect", "source": "/Volumes/data/Movies"}
            self.assertFalse(current.prepare("connect_local_1", "local_media", params))
            saved = json.loads(path.read_text())
            key = saved["pending"]["key"]
            self.assertNotIn("/Volumes", path.read_text())
            # The player applied the connection and journalled success, while
            # the direct reply was lost before the caller could complete it.
            self.sources = [source()]
            operations[key] = {"state": "responded", "result_status": "ok"}
            current.close()
            resumed = module.Session(ott, player, "dev_tv", "local", self.normalized(), path, True)
            try:
                self.assertTrue(resumed.prepare("connect_local_1", "local_media", params))
                self.assertIsNone(resumed.state["pending"])
                self.assertIn("connect_local_1", resumed.state["completed"])
                self.assertFalse(any(c["op"] == "connect" for c in self.local_calls))
            finally:
                resumed.close()


if __name__ == "__main__":
    unittest.main()
