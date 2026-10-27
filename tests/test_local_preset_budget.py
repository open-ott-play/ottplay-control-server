"""Bound remote reads without caching across mutations or verification phases."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("budget_fixtures", Path(__file__).with_name("test_presets_cli.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
ott = fixtures.ott


class LocalPlayer(fixtures.FakePlayer):
    def __init__(self, count=32):
        super().__init__()
        self.sources = [{"id": f"folder-{n}", "name": f"Movies{n}", "kind": "folder", "available": True,
                         "path": f"/Volumes/data/Movies{n}"} for n in range(count)]
        self.local_calls = []
        self.aliases = {}
        self.runtime = "page-123"
        self.response_runtime = None
        self.remove_during_settings = False

    def call(self, device, action, params, idempotency_key=None):
        if action in ("capabilities", "local_media"):
            self.local_calls.append((action, copy.deepcopy(params)))
        if action == "capabilities":
            return {"version": 1, "player": {"version": "test", "platform": "tauri", "runtime": self.runtime},
                    "lifecycle": [], "input": [], "playback": [],
                    "local_media": {"version": 1, "operations": ["sources", "connect"], "absolute_paths": True,
                                    "max_sources": 32, "max_items": 50000}}
        if action == "local_media":
            if params["op"] != "sources":
                raise AssertionError("Already connected folders must not be connected again")
            rows = self.sources
            if "path" in params:
                path = self.aliases.get(params["path"], params["path"]).rstrip("/\\")
                rows = [row for row in rows if row["path"].rstrip("/\\") == path]
            return {"version": 1, "runtime": self.response_runtime or params["runtime"], "op": "sources",
                    "state": "ready", "sources": copy.deepcopy(rows)}
        if action == "provider_settings" and self.remove_during_settings:
            self.sources[0]["available"] = False
        return super().call(device, action, params)

    def preset(self):
        config = fixtures.config()
        config["presets"]["local"]["local"] = {
            "sources": [{"name": row["name"], "path": row["path"]} for row in self.sources]}
        return ott.validate_preset(config, "local")[1]


class LocalPresetReadBudgetTests(unittest.TestCase):
    def test_32_connected_native_folders_use_two_fresh_snapshots(self):
        player = LocalPlayer()
        result = ott.load_preset(player, "dev_tv", "local", player.preset())
        self.assertEqual(result["status"], "loaded")
        self.assertEqual([action for action, _ in player.local_calls],
                         ["capabilities", "local_media", "capabilities", "local_media"])
        self.assertEqual(len(player.calls), 9)

    def test_only_noncanonical_spelling_requires_extra_target_lookup(self):
        player = LocalPlayer()
        preset = player.preset()
        alias = "/data-alias/Movies0/"
        preset["local"]["sources"][0]["path"] = alias
        player.aliases[alias] = player.sources[0]["path"]
        result = ott.load_preset(player, "dev_tv", "local", preset)
        self.assertEqual(result["status"], "loaded")
        self.assertEqual(len(player.local_calls), 6)
        self.assertEqual([params["path"] for _, params in player.local_calls if "path" in params], [alias, alias])

    def test_final_snapshot_detects_disconnected_folder(self):
        player = LocalPlayer()
        player.remove_during_settings = True
        result = ott.load_preset(player, "dev_tv", "local", player.preset())
        self.assertEqual((result["status"], result["stage"]), ("failed", "verify_local"))
        self.assertEqual(len(player.local_calls), 4)

    def test_cached_read_context_still_rejects_a_different_runtime(self):
        player = LocalPlayer(1)
        controls = ott.local_media_capabilities(player, "dev_tv", 45)
        player.response_runtime = "page-456"
        with self.assertRaisesRegex(ott.Error, "Invalid local media result"):
            ott.local_preset_snapshot(player, "dev_tv", controls=controls)
        self.assertEqual(len(player.local_calls), 2)


if __name__ == "__main__":
    unittest.main()
