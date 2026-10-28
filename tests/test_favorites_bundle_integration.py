"""Real codec/store/CLI boundaries using temporary private fixtures only."""
import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ott = load("ott_bundle_integration", "cli/ott.py")
codec = load("favorites_document_integration", "cli/favorites_document.py")
storage = load("preset_favorites_integration", "cli/preset_favorites.py")


def scope(source=1, rating="123"):
    return {"schema": 1, "scope": {
        "provider": "plex", "profileBindingId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "sourceBindingId": f"bbbbbbbb-bbbb-4bbb-8bbb-{source:012d}"},
        "coverage": {"channelLists": "complete-for-scope", "mediaFavorites": "complete-for-scope",
                     "remoteAliases": "excluded-preserved", "history": "excluded-preserved"},
        "channelLists": {"active": "Empty", "order": ["Empty"], "default": {"present": False},
                         "lists": [{"name": "Empty", "entries": [], "shuffleBefore": []}]},
        "mediaFavorites": [{"state": "located", "title": "Fixture title",
                            "locator": {"kind": "plex-media", "serverId": "fixture-pms", "ratingKey": rating}}]}


def bundle(*documents):
    return {"version": 1, "scopes": list(documents)}


class FavoritesBundleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.path = self.directory / "cli.json"
        self.original = {"presets": {"Home": {"fixture_settings": "retained"}},
                         "token": "fixture-private-original", "aliases": {"example": "fixture-device"},
                         "preset_favorites": {"version": 1, "presets": {"Home": bundle(scope())}}}
        self.write(self.path, self.original)
        self.dependencies = {"validate_document": codec.validate_document,
                             "planner": codec.plan_documents, "store_factory": storage.Store,
                             "storage_errors": (storage.StorageError,)}

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)

    def command(self, operation, **params):
        return ott.presets_favorites_command(self.path, {"op": operation, "preset": "home", **params},
                                            **self.dependencies)

    def test_replace_restore_only_selected_document_and_export_roundtrip(self):
        incoming = self.directory / "incoming.json"
        self.write(incoming, bundle(scope(rating="456")))
        result = self.command("import", path=str(incoming), mode="replace")
        self.assertEqual(result["status"], "imported")
        current = json.loads(self.path.read_text())
        self.assertEqual(current["token"], self.original["token"])
        current["token"] = "fixture-private-rotated"
        current["aliases"]["new"] = "fixture-other-device"
        self.write(self.path, current)
        restored = self.command("restore", path=result["backup_path"])
        self.assertEqual(restored["status"], "restored")
        current = json.loads(self.path.read_text())
        self.assertEqual(current["token"], "fixture-private-rotated")
        self.assertEqual(current["aliases"]["new"], "fixture-other-device")
        self.assertEqual(current["preset_favorites"], self.original["preset_favorites"])
        exported = self.directory / "export.json"
        self.command("export", path=str(exported))
        self.assertEqual(self.command("diff", path=str(exported))["status"], "matched")
        self.assertNotIn("fixture-private", exported.read_text())

    def test_conflicting_merge_preview_and_apply_never_write(self):
        incoming = self.directory / "incoming.json"
        changed = scope()
        changed["channelLists"]["lists"][0]["shuffleBefore"] = copy.deepcopy(changed["mediaFavorites"])
        self.write(incoming, bundle(changed))
        before = self.path.read_bytes()
        files = set(self.directory.iterdir())
        result = self.command("import", path=str(incoming), mode="merge", dry_run=True)
        self.assertEqual(result["status"], "blocked")
        self.assertGreater(result["summary"]["conflicts"]["unresolved"], 0)
        with self.assertRaisesRegex(ott.Error, "unresolved conflicts"):
            self.command("import", path=str(incoming), mode="merge")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(self.directory.iterdir()), files)

    def test_group_collect_deduplicates_devices_and_writes_one_bundle(self):
        seen = []

        def export(device, timeout):
            seen.append(device)
            return {"complete": True, "document": bundle(scope(source=2 if device == "two" else 3))}

        result = ott.collect_favorites_profile(self.path, "Home",
            [("a", "two"), ("same", "two"), ("b", "three")], 45,
            export_snapshot=export, **self.dependencies)
        self.assertEqual(seen, ["two", "three"])
        self.assertEqual(result["players"], 2)
        self.assertEqual(result["summary"]["after"], 3)
        self.assertEqual(len(list(self.directory.glob("cli.json.favorites-backup-*.json"))), 1)
        saved = json.loads(self.path.read_text())["preset_favorites"]["presets"]["Home"]
        self.assertEqual(len(saved["scopes"]), 3)

    def test_external_config_edit_during_collection_is_not_overwritten(self):
        updated = {**self.original, "token": "fixture-private-rotated"}

        def export(*_):
            self.write(self.path, updated)
            return {"complete": True, "document": bundle(scope(source=2))}

        with self.assertRaises(ott.Error):
            ott.collect_favorites_profile(self.path, "Home", [("a", "two")], 45,
                export_snapshot=export, **self.dependencies)
        self.assertEqual(json.loads(self.path.read_text()), updated)
        self.assertEqual(list(self.directory.glob("cli.json.favorites-backup-*.json")), [])


if __name__ == "__main__":
    unittest.main()
