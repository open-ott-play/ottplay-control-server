"""Offline favorites seams; fixture documents are not a production wire schema."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_favorites_profiles", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)


def document(*members):
    return {"test_schema": 1, "members": list(members)}


def validate(value):
    if (not isinstance(value, dict) or set(value) != {"test_schema", "members"}
            or value["test_schema"] != 1 or not isinstance(value["members"], list)
            or any(not isinstance(item, str) for item in value["members"])
            or len(set(value["members"])) != len(value["members"])):
        raise ValueError("private malformed document")
    return copy.deepcopy(value)


def merge(current, incoming_documents, mode):
    existing = current["members"] if current is not None else []
    incoming = [member for value in incoming_documents for member in value["members"]]
    result = list(dict.fromkeys([*(existing if mode == "merge" else []), *incoming]))
    before, after = set(existing), set(result)
    counts = {"before": len(before), "after": len(after), "added": len(after - before),
              "removed": len(before - after), "retained": len(before & after)}
    duplicate_count = len(incoming) - len(set(incoming) - (before if mode == "merge" else set()))
    return {"document": document(*result), "summary": {
        **counts, "skipped_duplicates": duplicate_count, "memberships": dict(counts),
        "conflicts": {"resolved": 0, "unresolved": 0}}}


class FixtureStore:
    def __init__(self, current, events=None):
        self.current = copy.deepcopy(current)
        self.events = events if events is not None else []
        self.revision = "a" * 64
        self.plans, self.applies = [], []

    def snapshot(self, name):
        self.events.append(("snapshot", name))
        return {"preset": "Home", "document": copy.deepcopy(self.current),
                "config_revision": self.revision, "orphans": []}

    def plan(self, name, value, *, expected_revision=None):
        self.events.append(("plan", name))
        if expected_revision != self.revision:
            raise ott.Error("Configuration changed; no local write")
        summary = {"preset": name, "source_revision": expected_revision, "changed": value != self.current}
        plan = SimpleNamespace(document=copy.deepcopy(value), changed=summary["changed"], summary=lambda: dict(summary))
        self.plans.append(plan)
        return plan

    def apply(self, plan):
        self.events.append(("apply", "Home"))
        self.applies.append(plan)
        self.current = copy.deepcopy(plan.document)
        return {"preset": "Home", "changed": plan.changed, "config_revision": "b" * 64, "backup_path": None}


class FavoritesProfileParserTests(unittest.TestCase):
    def test_pure_preset_resolver_preserves_errors_without_provider_recursion(self):
        config = {"presets": {"Home": {"provider-schema-not-needed": True}}}
        with mock.patch.object(ott, "validate_preset", side_effect=AssertionError("recursive provider validation")):
            self.assertEqual(ott.resolve_preset_name(config, "hOME"), "Home")
            factory = mock.Mock(return_value=object())
            ott._favorites_profile_store("unused.json", validate, factory)
            resolver = factory.call_args.kwargs["resolve_preset"]
            self.assertIs(resolver, ott.resolve_preset_name)
            self.assertEqual(resolver(config, "home"), "Home")
        for value, requested, message in (
            (config, "../Home", "Invalid preset name"),
            (config, "Missing", "Unknown preset; use ott presets to list local names"),
            ({"presets": {"Home": {}, "home": {}}}, "Home", "Preset names must be unique ignoring case"),
            ({"presets": {"invalid/name": {}}}, "invalid/name", "Presets must be a mapping of short names using letters, digits, underscores or hyphens"),
        ):
            with self.assertRaises(ott.Error) as error:
                ott.resolve_preset_name(value, requested)
            self.assertEqual(str(error.exception), message)

    def test_absent_companion_preserves_legacy_normalization_and_checkpoint_digest(self):
        config = {"presets": {"Home": {
            "m3u": [{"number": 1, "name": "Home", "playlist": "https://example.invalid/live.m3u", "history_hours": 24}],
            "plex": {"server": "https://plex.invalid", "token": "fixture"}, "active_profile": 1}}}
        expected = {"m3u": [{"number": 1, "settings": {"name": "Home", "playlist": "https://example.invalid/live.m3u", "history_hours": 24}}],
                    "plex": {"server": "https://plex.invalid", "token": "fixture"}, "active_profile": 1}
        spec = importlib.util.spec_from_file_location("favorites_legacy_preset_session_digest", ROOT / "cli/preset_session.py")
        session = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(session)
        for extra in ({}, {"preset_favorites": {"version": 1, "presets": {"Orphan": {"future-document": True}}}},
                      {"preset_favorites": {"version": 999, "presets": {"Other": {"future-document": True}}}}):
            with mock.patch.object(ott, "_favorites_profile_store", side_effect=AssertionError("unexpected favorites helper")), \
                    mock.patch.object(ott.importlib.util, "spec_from_file_location", side_effect=AssertionError("unexpected module load")):
                name, result = ott.validate_preset({**copy.deepcopy(config), **extra}, "HOME")
            self.assertEqual((name, result), ("Home", expected))
            self.assertEqual(session.digest(result), session.digest(expected))
            self.assertNotIn("favorites", result)
            self.assertNotIn("preset_favorites", result)

    def test_new_aliases_and_explicit_legacy_names(self):
        for alias in ("fav", "favs", "favorite", "favorites", "favourite", "favourites", "FAV"):
            for operation in ("load", "collect"):
                self.assertEqual(ott.parse_command([alias, operation, "Home"]),
                                 ("favorites_profile", {"op": operation, "preset": "Home", "mode": "merge", "dry_run": False}))
                self.assertEqual(ott.parse_command([alias, operation.upper(), "Home", "REPLACE", "--dry-run"]),
                                 ("favorites_profile", {"op": operation, "preset": "Home", "mode": "replace", "dry_run": True}))
            self.assertEqual(ott.parse_command([alias, "diff", "Home"])[1], {"op": "diff", "preset": "Home", "dry_run": True})
            self.assertEqual(ott.parse_command([alias, "clear", "--dry-run"])[1], {"op": "clear", "dry_run": True})
            self.assertEqual(ott.parse_command([alias, "clear"])[1], {"op": "clear", "dry_run": False})
            for name in ("load", "collect", "diff", "clear"):
                self.assertEqual(ott.parse_command([alias, "play", name]), ("favorites", {"op": "play", "query": name}))
            self.assertEqual(ott.parse_command([alias, "add", "clear"]), ("favorites", {"op": "add", "name": "clear"}))
        self.assertEqual(ott.FAVORITES_OPERATIONS, {"list", "add", "play", "remove"})
        self.assertNotIn("favorites_profile", ott.RECEIPT_ACTIONS)

    def test_present_companion_cannot_be_ignored_by_preset_load(self):
        base = {"presets": {"Home": {
            "m3u": [{"number": 1, "name": "Home", "playlist": "https://example.invalid/live.m3u", "history_hours": 24}],
            "plex": {"server": "https://plex.invalid", "token": "fixture-private-token"}, "active_profile": 1}}}
        for companion in ({"version": 1, "scopes": []}, None, {"private": "fixture-private-token"}):
            for mode in ([], ["plan"], ["apply"], ["resume"], ["verify"]):
                config = {**copy.deepcopy(base), "preset_favorites": {"version": 1, "presets": {"Home": companion}}}
                before = copy.deepcopy(config)
                output, error = io.StringIO(), io.StringIO()
                with self.subTest(mode=mode, companion=companion), \
                        mock.patch.object(ott, "read_json", return_value=config), \
                        mock.patch.object(ott, "Client") as client, \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    self.assertEqual(ott.main(["-j", "t1", "load", "HOME", *mode]), 1)
                    client.assert_not_called()
                self.assertIn("complete preset favorites delivery is not available", output.getvalue() + error.getvalue())
                self.assertNotIn("fixture-private-token", output.getvalue() + error.getvalue())
                self.assertEqual(config, before)
        config = {**copy.deepcopy(base), "preset_favorites": {"version": 1, "presets": {"Home": {}}}}
        args = SimpleNamespace(words=["t1,t2", "load", "Home"], json=False)
        client = mock.Mock()
        with self.assertRaisesRegex(ott.Error, "complete preset favorites delivery"):
            ott.player_group_command(client, config, [("t1", "one"), ("t2", "two")], args, [])
        self.assertEqual(client.mock_calls, [])

    def test_preset_companion_gate_preserves_absent_and_orphan_only_behavior(self):
        for config in ({}, {"preset_favorites": {"version": 999, "presets": {"Other": {"future": True}}}}):
            with mock.patch.object(ott, "favorites_profile_dependencies", side_effect=AssertionError("unneeded helper")):
                self.assertIsNone(ott.require_preset_favorites_support(config, "Home"))
        for extension in (None, [], {"version": 1}, {"presets": []}):
            with self.assertRaisesRegex(ott.Error, "Invalid saved favorites metadata"):
                ott.require_preset_favorites_support({"preset_favorites": extension}, "Home")
        with self.assertRaisesRegex(ott.Error, "complete preset favorites delivery"):
            ott.require_preset_favorites_support({"preset_favorites": {"presets": {"hOME": {}}}}, "Home")

    def test_bad_player_grammar_never_becomes_search(self):
        for words in (("load",), ("load", "Home", "invalid"), ("load", "../Home"),
                      ("load", "Home", "--dry-run", "replace"), ("load", "Home", "--dry-run", "--dry-run"),
                      ("collect",), ("collect", "Home", "merge", "replace"),
                      ("diff",), ("diff", "Home", "replace"), ("clear", "all"), ("clear", "--force")):
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(["fav", *words])

    def test_offline_grammar(self):
        self.assertEqual(ott.parse_presets_command([]), {"op": "list"})
        for alias in ("fav", "favorites", "FAVOURITES"):
            self.assertEqual(ott.parse_presets_command(["Home", alias, "validate"]), {"op": "validate", "preset": "Home"})
            for operation in ("export", "diff"):
                self.assertEqual(ott.parse_presets_command(["Home", alias, operation, "./-private.json"]),
                                 {"op": operation, "preset": "Home", "path": "./-private.json"})
            self.assertEqual(ott.parse_presets_command(["Home", alias, "import", "private.json"])["mode"], "merge")
            self.assertEqual(ott.parse_presets_command(["Home", alias, "import", "private.json", "replace", "--dry-run"]),
                             {"op": "import", "preset": "Home", "path": "private.json", "mode": "replace", "dry_run": True})
            for suffix in ([], ["--dry-run"]):
                self.assertEqual(ott.parse_presets_command(["Home", alias, "restore", "backup.json", *suffix]),
                                 {"op": "restore", "preset": "Home", "path": "backup.json", "dry_run": bool(suffix)})
        for words in (("Home",), ("Home", "fav", "validate", "extra"), ("Home", "fav", "export"),
                      ("Home", "fav", "diff", "x.json", "merge"), ("Home", "fav", "import", "x.json", "delete"),
                      ("Home", "fav", "export", "-token"), ("Home", "fav", "export", "directory/"),
                      ("Home", "fav", "export", "bad\nname.json"),
                      ("Home", "fav", "import", "x.json", "--dry-run", "replace"),
                      ("Home", "fav", "import", "x.json", "--dry-run", "--dry-run"),
                      ("Home", "fav", "restore", "backup.json", "merge"),
                      ("Home", "fav", "restore", "backup.json", "replace"),
                      ("Home", "fav", "restore", "backup.json", "--dry-run", "--dry-run")):
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_presets_command(list(words))

    def test_unwired_commands_fail_before_client_or_writes(self):
        for words in (["t1", "fav", "load", "Home"], ["t1,t2", "fav", "collect", "Home"],
                      ["t1", "fav", "clear"], ["t1", "fav", "clear", "--dry-run"]):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(ott, "read_json", return_value={"presets": {"Home": {}}}), \
                    mock.patch.object(ott, "Client") as client, mock.patch.object(ott, "write_private") as write, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(ott.main(words), 1)
                client.assert_not_called()
                write.assert_not_called()
            self.assertEqual(out.getvalue(), "")
            self.assertIn("not available", err.getvalue())
        with mock.patch.object(ott, "read_json", return_value={"presets": {"Home": {}}}), \
                mock.patch.object(ott, "Client") as client, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ott.main(["presets"]), 0)
            client.assert_not_called()
            self.assertEqual(out.getvalue(), "Home\n")


class FavoritesProfileOutputTests(unittest.TestCase):
    def result(self):
        return {"op": "import", "status": "planned", "preset": "Home",
                "summary": merge(document("old/item"), [document("new/item")], "replace")["summary"]}

    def render(self, result):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            ott.print_favorites_profile(result)
        return output.getvalue()

    def test_readable_counts_preview_and_clear_without_private_payload(self):
        value = dict(self.result(), document={"token": "private-payload"}, path="private-remote-path", source_revision="private-hash")
        text = self.render(value)
        self.assertIn("Favorites profile Home: preview only; nothing changed.", text)
        self.assertIn("Items: 1 -> 1; added 1, removed 1, retained 0.", text)
        self.assertIn("List memberships: 1 -> 1", text)
        self.assertIn("Duplicate items skipped: 0.", text)
        self.assertNotIn("private-", text)
        self.assertNotIn("{", text)
        cleared = dict(value, op="clear", status="cleared", preset=None)
        self.assertIn("Visible favorites: cleared.", self.render(cleared))

    def test_only_explicit_local_export_and_backup_paths_are_printed(self):
        self.assertEqual(self.render({"op": "validate", "status": "valid", "preset": "Home"}), "Favorites profile Home: valid.\n")
        self.assertIn("File: /tmp/favorites.json", self.render({"op": "export", "status": "exported", "preset": "Home", "path": "/tmp/favorites.json"}))
        self.assertIn("Backup: /tmp/local-backup.json", self.render(dict(self.result(), backup_path="/tmp/local-backup.json")))
        self.assertNotIn("remote-backup", self.render(dict(self.result(), op="load", status="loaded", backup_path="remote-backup")))

    def test_invalid_metadata_is_rejected_before_any_partial_output(self):
        variants = [dict(self.result(), status="private-status"), dict(self.result(), preset="private\nname"),
                    dict(self.result(), players=True), dict(self.result(), backup_path="private\x1bpath")]
        invalid = self.result()
        invalid["summary"]["added"] = True
        variants.append(invalid)
        for value in variants:
            with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(ott.Error) as error:
                ott.print_favorites_profile(value)
            self.assertEqual(output.getvalue(), "")
            self.assertNotIn("private-", str(error.exception))

    def test_offline_cli_plain_default_and_explicit_json_are_separate(self):
        result = dict(self.result(), op="diff", status="different")
        for json_output in (False, True):
            with mock.patch.object(ott, "read_json", return_value={"presets": {"Home": {}}}), \
                    mock.patch.object(ott, "presets_favorites_command", return_value=result), \
                    mock.patch.object(ott, "Client") as client, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(ott.main((["-j"] if json_output else []) + ["presets", "Home", "fav", "diff", "input.json"]), 0)
                client.assert_not_called()
            if json_output:
                self.assertEqual(json.loads(output.getvalue()), result)
            else:
                self.assertIn("Favorites profile Home: differences found.", output.getvalue())
                self.assertNotIn("{", output.getvalue())

    def test_blocked_preview_exits_nonzero_without_client_in_plain_and_json(self):
        result = dict(self.result(), status="blocked")
        result["summary"]["conflicts"]["unresolved"] = 1
        for json_output in (False, True):
            with mock.patch.object(ott, "read_json", return_value={"presets": {"Home": {}}}), \
                    mock.patch.object(ott, "presets_favorites_command", return_value=result), \
                    mock.patch.object(ott, "Client") as client, contextlib.redirect_stdout(io.StringIO()) as output:
                args = (["-j"] if json_output else []) + ["presets", "Home", "fav", "import", "input.json", "--dry-run"]
                self.assertEqual(ott.main(args), 1)
                client.assert_not_called()
            if json_output:
                self.assertEqual(json.loads(output.getvalue()), result)
            else:
                self.assertIn("unresolved conflicts; nothing changed", output.getvalue())


class FavoritesProfileAdapterTests(unittest.TestCase):
    def dependencies(self, store):
        return {"validate_document": validate, "planner": merge, "store_factory": mock.Mock(return_value=store)}

    def test_import_merge_replace_diff_and_noop_use_snapshot_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            path.write_text(json.dumps(document("source/a", "source/b")))
            for operation, mode, expected in (("import", "merge", ["other/item", "source/a", "source/b"]),
                                              ("import", "replace", ["source/a", "source/b"]),
                                              ("diff", None, ["source/a", "source/b"])):
                store = FixtureStore(document("other/item", "source/a"))
                params = {"op": operation, "preset": "home", "path": str(path)}
                if mode:
                    params["mode"] = mode
                result = ott.presets_favorites_command("config.json", params, **self.dependencies(store))
                self.assertEqual(store.plans[0].document, document(*expected))
                self.assertEqual(len(store.applies), int(operation == "import"))
                self.assertNotIn("test_schema", json.dumps(result))
                self.assertNotIn("source/a", json.dumps(result))
                self.assertEqual(result["summary"]["before"], 2)
                self.assertEqual(result["summary"]["after"], len(expected))
            store = FixtureStore(document("source/a", "source/b"))
            result = ott.presets_favorites_command("config.json", {"op": "import", "preset": "Home", "path": str(path)}, **self.dependencies(store))
            self.assertEqual(result["status"], "unchanged")

    def test_validate_export_private_exclusive_and_missing_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.json"
            store = FixtureStore(document("source/item"))
            dependencies = self.dependencies(store)
            self.assertEqual(ott.presets_favorites_command("config.json", {"op": "validate", "preset": "Home"}, **dependencies)["status"], "valid")
            params = {"op": "export", "preset": "Home", "path": str(path)}
            self.assertEqual(ott.presets_favorites_command("config.json", params, **dependencies)["status"], "exported")
            self.assertEqual(json.loads(path.read_text()), store.current)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            original = path.read_bytes()
            store.current = document("different/source/item")
            with self.assertRaises(ott.Error) as error:
                ott.presets_favorites_command("config.json", params, **dependencies)
            self.assertEqual(str(error.exception), "Export file already exists; choose a new path. The existing file was not changed")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual((store.plans, store.applies), ([], []))
            with self.assertRaisesRegex(ott.Error, "no saved"):
                ott.presets_favorites_command("config.json", params, **self.dependencies(FixtureStore(None)))
            oversized = Path(directory) / "oversized.json"
            with self.assertRaisesRegex(ott.Error, "size limit"):
                ott.presets_favorites_command("config.json", dict(params, path=str(oversized)),
                                             **self.dependencies(FixtureStore(document("x" * ott.MAX_FAVORITES_PROFILE_FILE_BYTES))))
            self.assertFalse(oversized.exists())

    def test_export_filesystem_errors_are_actionable_and_redacted(self):
        store = FixtureStore(document("private-favorite"))
        params = {"op": "export", "preset": "Home", "path": "fixture-export.json"}
        for failure in (PermissionError("private filesystem detail"), OSError("private payload detail")):
            with mock.patch.object(ott, "write_private", side_effect=failure) as write, self.assertRaises(ott.Error) as error:
                ott.presets_favorites_command("config.json", params, **self.dependencies(store))
            self.assertEqual(str(error.exception), "Could not create the favorites export file")
            write.assert_called_once_with("fixture-export.json", store.current, exclusive=True, compact=True)
            self.assertEqual((store.plans, store.applies), ([], []))

    def test_private_writer_default_format_is_unchanged_and_compact_is_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            value = {"title": "é", "members": ["one", "two"]}
            pretty = Path(directory) / "pretty.json"
            compact = Path(directory) / "compact.json"
            ott.write_private(pretty, value)
            ott.write_private(compact, value, exclusive=True, compact=True)
            self.assertEqual(pretty.read_text(encoding="utf-8"), json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            self.assertEqual(compact.read_bytes(), json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            with self.assertRaises(FileExistsError):
                ott.write_private(compact, {"changed": True}, exclusive=True, compact=True)
            self.assertEqual(json.loads(compact.read_text(encoding="utf-8")), value)

    def test_bad_import_never_plans_writes_or_echoes_private_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            for raw in (b'{"test_schema":1,"members":[],"members":[]}', b'{"members":[NaN]}',
                        b'{"private-token":"secret"}', b'"' + b'x' * (1024 * 1024) + b'"'):
                path.write_bytes(raw)
                store = FixtureStore(document("existing/item"))
                with self.assertRaises(ott.Error) as error:
                    ott.presets_favorites_command("config.json", {"op": "import", "preset": "Home", "path": str(path)}, **self.dependencies(store))
                self.assertNotIn("secret", str(error.exception))
                self.assertNotIn("private malformed", str(error.exception))
                self.assertEqual((store.plans, store.applies), ([], []))

    def test_collect_all_complete_snapshots_deduplicates_and_writes_once(self):
        events = []
        store = FixtureStore(document("existing/item"), events)

        def export(device, timeout):
            events.append(("export", device))
            self.assertEqual(timeout, 45)
            return {"complete": True, "document": document("shared/item", device + "/item")}

        result = ott.collect_favorites_profile("config.json", "Home", [("t1", "one"), ("same", "one"), ("t2", "two")],
                                              45, export_snapshot=export, **self.dependencies(store))
        self.assertEqual(events, [("snapshot", "Home"), ("export", "one"), ("export", "two"), ("plan", "Home"), ("apply", "Home")])
        self.assertEqual(store.current, document("existing/item", "shared/item", "one/item", "two/item"))
        self.assertEqual((result["players"], len(store.applies)), (2, 1))

    def test_collect_failure_deadline_and_config_race_write_nothing(self):
        for failure in ("partial", "error", "deadline", "race"):
            store = FixtureStore(document("existing/item"))

            def export(device, timeout, case=failure, target_store=store):
                if device == "two":
                    if case == "error":
                        raise ott.Error("Player snapshot failed")
                    if case == "race":
                        target_store.revision = "c" * 64
                    if case == "partial":
                        return {"complete": False, "document": document("partial/item")}
                return {"complete": True, "document": document(device + "/item")}

            times = [0, 1, 2, 48] if failure == "deadline" else [0, 1, 2, 3]
            with mock.patch.object(ott.time, "monotonic", side_effect=times), self.assertRaises(ott.Error):
                ott.collect_favorites_profile("config.json", "Home", [("t1", "one"), ("t2", "two")],
                                              45, export_snapshot=export, **self.dependencies(store))
            self.assertEqual(store.applies, [])
            self.assertEqual(store.current, document("existing/item"))

    def test_collect_replace_dry_run_has_no_old_members_or_write(self):
        store = FixtureStore(document("old/item"))
        result = ott.collect_favorites_profile("config.json", "Home", [("t1", "one")], 45,
                                              mode="replace", dry_run=True,
                                              export_snapshot=lambda *_: {"complete": True, "document": document("new/item")},
                                              **self.dependencies(store))
        self.assertEqual(result["status"], "planned")
        self.assertEqual(store.plans[0].document, document("new/item"))
        self.assertEqual(store.applies, [])

    def test_group_replace_plans_once_against_original_and_resolves_duplicate_device_once(self):
        store = FixtureStore(document("old/item", "keep/item"))
        planner = mock.Mock(side_effect=merge)
        exporter = mock.Mock(side_effect=[
            {"complete": True, "document": document("keep/item", "new/item")},
            {"complete": True, "document": document("keep/item", "third/item")},
        ])
        dependencies = dict(self.dependencies(store), planner=planner)
        result = ott.collect_favorites_profile("config.json", "Home", [("t1", "one"), ("same", "one"), ("t2", "two")],
                                              45, mode="replace", export_snapshot=exporter, **dependencies)
        exporter.assert_has_calls([mock.call("one", 45), mock.call("two", 45)])
        self.assertEqual(exporter.call_count, 2)
        planner.assert_called_once_with(document("old/item", "keep/item"),
                                        [document("keep/item", "new/item"), document("keep/item", "third/item")], "replace")
        self.assertEqual(result["summary"], {
            "before": 2, "after": 3, "added": 2, "removed": 1, "retained": 1, "skipped_duplicates": 1,
            "memberships": {"before": 2, "after": 3, "added": 2, "removed": 1, "retained": 1},
            "conflicts": {"resolved": 0, "unresolved": 0}})
        self.assertEqual(len(store.applies), 1)

    def test_item_and_membership_units_are_independent(self):
        store = FixtureStore(document("item/list-a"))
        plan = {"document": document("item/list-a", "item/list-b"), "summary": {
            "before": 1, "after": 1, "added": 0, "removed": 0, "retained": 1, "skipped_duplicates": 0,
            "memberships": {"before": 1, "after": 2, "added": 1, "removed": 0, "retained": 1},
            "conflicts": {"resolved": 0, "unresolved": 0}}}
        result = ott.collect_favorites_profile("config.json", "Home", [("t1", "one")], 45,
                                              export_snapshot=lambda *_: {"complete": True, "document": document("item/list-b")},
                                              **dict(self.dependencies(store), planner=lambda *_: plan))
        self.assertEqual((result["summary"]["after"], result["summary"]["memberships"]["after"]), (1, 2))

    def test_invalid_private_or_inconsistent_summary_never_applies(self):
        mutations = [
            lambda value: value.update(private_token="secret"),
            lambda value: value["summary"].update(private_token="secret"),
            lambda value: value["summary"].update(added=True),
            lambda value: value["summary"].update(added=-1),
            lambda value: value["summary"].update(skipped_duplicates=1.5),
            lambda value: value["summary"].update(before=7),
            lambda value: value["summary"]["memberships"].update(after=8),
            lambda value: value["summary"]["memberships"].update(retained=False),
            lambda value: value["summary"]["conflicts"].update(unresolved=True),
            lambda value: value["summary"]["conflicts"].update(reason="secret"),
        ]
        for mutate in mutations:
            store = FixtureStore(document("old/item"))
            planned = merge(store.current, [document("new/item")], "merge")
            mutate(planned)
            with self.assertRaises(ott.Error) as error:
                ott.collect_favorites_profile("config.json", "Home", [("t1", "one")], 45,
                                              export_snapshot=lambda *_: {"complete": True, "document": document("new/item")},
                                              **dict(self.dependencies(store), planner=lambda *_, value=planned: value))
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual((store.plans, store.applies), ([], []))

    def test_unresolved_conflicts_can_preview_but_cannot_collect_or_import(self):
        def conflict_planner(*args):
            result = merge(*args)
            result["summary"]["conflicts"]["unresolved"] = 1
            return result

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(document("new/item")))
            for command in ("collect", "import"):
                for dry_run in (False, True):
                    store = FixtureStore(document("old/item"))
                    dependencies = dict(self.dependencies(store), planner=conflict_planner)

                    def execute(operation=command, preview=dry_run, options=dependencies):
                        if operation == "import":
                            return ott.presets_favorites_command("config.json", {"op": "import", "preset": "Home", "path": str(path), "dry_run": preview}, **options)
                        return ott.collect_favorites_profile("config.json", "Home", [("t1", "one")], 45, dry_run=preview,
                                                              export_snapshot=lambda *_: {"complete": True, "document": document("new/item")}, **options)

                    if dry_run:
                        self.assertEqual(execute()["status"], "blocked")
                    else:
                        with self.assertRaisesRegex(ott.Error, "unresolved conflicts"):
                            execute()
                    self.assertEqual(store.applies, [])

    def test_typed_storage_uncertainty_never_claims_rollback(self):
        class StorageError(Exception):
            replacement_occurred = True

        store = FixtureStore(document("old/item"))
        store.apply = mock.Mock(side_effect=StorageError("private path"))
        with self.assertRaisesRegex(ott.Error, "uncertain outcome") as error:
            ott.collect_favorites_profile("config.json", "Home", [("t1", "one")], 45,
                                          export_snapshot=lambda *_: {"complete": True, "document": document("new/item")},
                                          storage_errors=(StorageError,), **self.dependencies(store))
        self.assertNotIn("private path", str(error.exception))
        store.apply.assert_called_once()

    def test_planner_and_validator_unexpected_exceptions_are_private(self):
        for component in ("planner", "validate_document"):
            for exception in (ott.Error("private-token"), RuntimeError("private-token"), RecursionError("private-token")):
                store = FixtureStore(document("old/item"))
                dependencies = dict(self.dependencies(store), **{component: mock.Mock(side_effect=exception)})
                with self.assertRaises(ott.Error) as error:
                    ott.collect_favorites_profile("config.json", "Home", [("t1", "one")], 45,
                                                  export_snapshot=lambda *_: {"complete": True, "document": document("new/item")},
                                                  **dependencies)
                self.assertNotIn("private-token", str(error.exception))
                self.assertEqual((store.plans, store.applies), ([], []))

    def test_exporter_exception_text_is_private_and_request_metadata_is_retained(self):
        request = ott.RequestError("controller_unavailable", "receipt", "private-token", "a" * 32, transport_category="socket")
        request.action = "favorites"
        for exception in (ott.Error("private-token"), RuntimeError("private-token"), ValueError("private-token"), request):
            store = FixtureStore(document("old/item"))
            with self.assertRaises(ott.Error) as error:
                ott.collect_favorites_profile("config.json", "Home", [("t1", "one")], 45,
                                              export_snapshot=mock.Mock(side_effect=exception), **self.dependencies(store))
            self.assertNotIn("private-token", str(error.exception))
            self.assertEqual((store.plans, store.applies), ([], []))
            if isinstance(exception, ott.RequestError):
                self.assertEqual(ott.request_error_metadata(error.exception), {
                    "action": "favorites", "request_id": "a" * 32, "stage": "receipt",
                    "reason": "controller_unavailable", "transport_category": "socket"})

    def test_import_nonregular_files_are_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = [root]
            if hasattr(os, "mkfifo"):
                fifo = root / "input.fifo"
                os.mkfifo(fifo)
                candidates.append(fifo)
            if os.name != "nt":
                target = root / "private.json"
                target.write_text(json.dumps(document("item")))
                symlink = root / "symlink.json"
                symlink.symlink_to(target)
                candidates.append(symlink)
            for candidate in candidates:
                with self.subTest(path=candidate.name), mock.patch.object(ott.os, "open") as opened, self.assertRaises(ott.Error):
                    ott._read_favorites_document(candidate)
                opened.assert_not_called()

    @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"), "POSIX FIFO race")
    def test_regular_input_replaced_by_fifo_does_not_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(document("item")))
            original_open = os.open

            def replace_before_open(filename, flags):
                self.assertTrue(flags & os.O_NONBLOCK)
                self.assertTrue(flags & os.O_NOFOLLOW)
                path.unlink()
                os.mkfifo(path)
                return original_open(filename, flags)

            with mock.patch.object(ott.os, "open", side_effect=replace_before_open), self.assertRaises(ott.Error):
                ott._read_favorites_document(path)

    def test_injected_handler_uses_real_store_outer_extension_and_noop(self):
        spec = importlib.util.spec_from_file_location("favorites_profile_storage_adapter_test", ROOT / "cli/preset_favorites.py")
        storage = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = storage
        spec.loader.exec_module(storage)
        config = {"private_fixture": "preserve", "presets": {"Home": {
            "m3u": [{"number": 1, "name": "Home", "playlist": "https://example.invalid/live.m3u", "history_hours": 24}],
            "plex": {"server": "https://plex.invalid", "token": "fixture"}, "active_profile": 1}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cli.json"
            path.write_text(json.dumps(config))
            path.chmod(0o600)
            source = Path(directory) / "input.json"
            source.write_text(json.dumps(document("source/item")))
            params = {"op": "import", "preset": "home", "path": str(source), "mode": "merge"}
            dependencies = {"validate_document": validate, "planner": merge, "store_factory": storage.Store,
                            "storage_errors": (storage.StorageError,)}
            initial_files, initial_stat = sorted(Path(directory).iterdir()), path.stat()
            preview = ott.presets_favorites_command(str(path), dict(params, dry_run=True), **dependencies)
            self.assertEqual((preview["status"], preview["summary"]["added"]), ("planned", 1))
            self.assertEqual(sorted(Path(directory).iterdir()), initial_files)
            self.assertEqual((path.stat().st_mtime_ns, path.stat().st_size), (initial_stat.st_mtime_ns, initial_stat.st_size))
            self.assertEqual(json.loads(path.read_text()), config)
            result = ott.presets_favorites_command(str(path), params, **dependencies)
            self.assertEqual((result["preset"], result["status"]), ("Home", "imported"))
            saved = json.loads(path.read_text())
            self.assertEqual(saved["preset_favorites"], {"version": 1, "presets": {"Home": document("source/item")}})
            saved.pop("preset_favorites")
            self.assertEqual(saved, config)
            files, timestamp = sorted(Path(directory).iterdir()), path.stat().st_mtime_ns
            self.assertEqual(ott.presets_favorites_command(str(path), params, **dependencies)["status"], "unchanged")
            self.assertEqual((sorted(Path(directory).iterdir()), path.stat().st_mtime_ns), (files, timestamp))


class FavoritesProfileRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("favorites_profile_restore_storage_test", ROOT / "cli/preset_favorites.py")
        cls.storage = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.storage
        spec.loader.exec_module(cls.storage)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "cli.json"
        self.config = {"presets": {"Home": {"provider_fixture": "old"}, "Other": {}},
                       "private_fixture": "old", "players": {"t1": "old-device"}}
        self.write_config(self.config)
        self.dependencies = {"validate_document": validate, "planner": merge,
                             "store_factory": self.storage.Store, "storage_errors": (self.storage.StorageError,)}
        self.store = self.storage.Store(self.path, preset_names=ott.preset_names,
                                       resolve_preset=ott.resolve_preset_name, validate_document=validate)

    def write_config(self, value):
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)

    def restore(self, backup, **params):
        return ott.presets_favorites_command(str(self.path),
                                             {"op": "restore", "preset": "home", "path": str(backup), **params},
                                             **self.dependencies)

    def test_restore_only_selected_favorites_and_preview_without_writes(self):
        self.config["preset_favorites"] = {"version": 1, "presets": {"Home": document("shared", "original")}}
        self.write_config(self.config)
        outcome = self.store.apply(self.store.plan("Home", document("shared", "new", "extra")))
        old_backup = Path(outcome["backup_path"])
        current = json.loads(self.path.read_bytes())
        current["private_fixture"] = "current-private-settings"
        current["presets"]["Home"] = {"provider_fixture": "current-provider"}
        current["players"]["t1"] = "current-device"
        current["preset_favorites"]["presets"]["Other"] = document("preserve-other-favorite")
        self.write_config(current)
        before, timestamp = self.path.read_bytes(), self.path.stat().st_mtime_ns
        files = sorted(self.directory.iterdir())
        preview = self.restore(old_backup, dry_run=True)
        self.assertEqual(preview["status"], "planned")
        self.assertEqual(preview["summary"], merge(document("shared", "new", "extra"),
                                                  [document("shared", "original")], "replace")["summary"])
        self.assertEqual(preview["backup_revision"], hashlib.sha256(old_backup.read_bytes()).hexdigest())
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns, sorted(self.directory.iterdir())),
                         (before, timestamp, files))
        result = self.restore(old_backup)
        expected = copy.deepcopy(current)
        expected["preset_favorites"]["presets"]["Home"] = document("shared", "original")
        self.assertEqual(json.loads(self.path.read_bytes()), expected)
        self.assertEqual((result["status"], result["summary"]), ("restored", preview["summary"]))
        self.assertEqual(Path(result["backup_path"]).read_bytes(), before)
        self.assertNotIn("current-private-settings", json.dumps(result))
        self.assertNotIn("original", json.dumps(result))
        files, timestamp = sorted(self.directory.iterdir()), self.path.stat().st_mtime_ns
        self.assertEqual(self.restore(old_backup)["status"], "unchanged")
        self.assertEqual((sorted(self.directory.iterdir()), self.path.stat().st_mtime_ns), (files, timestamp))

    def test_missing_backup_document_never_means_clear(self):
        backup = self.store.apply(self.store.plan("Home", document("keep")))["backup_path"]
        before, files = self.path.read_bytes(), sorted(self.directory.iterdir())
        planner = mock.Mock(wraps=merge)
        self.dependencies["planner"] = planner
        for dry_run in (False, True):
            with self.assertRaisesRegex(ott.Error, "backup has no favorites document"):
                self.restore(backup, dry_run=dry_run)
        planner.assert_not_called()
        self.assertEqual((self.path.read_bytes(), sorted(self.directory.iterdir())), (before, files))

    def test_restore_snapshot_revision_rejects_concurrent_local_change(self):
        store = FixtureStore(document("current"))

        def backup_document(name, path):
            self.assertEqual((name, path), ("Home", "backup.json"))
            store.revision = "c" * 64
            return {"preset": "Home", "document": document("old"), "backup_revision": "d" * 64}

        store.backup_document = backup_document
        with self.assertRaisesRegex(ott.Error, "Configuration changed"):
            ott.presets_favorites_command("unused.json", {"op": "restore", "preset": "Home", "path": "backup.json"},
                                         validate_document=validate, planner=merge, store_factory=mock.Mock(return_value=store))
        self.assertEqual(store.applies, [])

    def test_invalid_backup_result_is_rejected_before_planning(self):
        good = {"preset": "Home", "document": document("old"), "backup_revision": "d" * 64}
        for value in (dict(good, preset="Other"), dict(good, backup_revision=True),
                      dict(good, backup_revision="D" * 64), dict(good, document=None), dict(good, extra="private")):
            store = FixtureStore(document("current"))
            store.backup_document = mock.Mock(return_value=value)
            planner = mock.Mock(wraps=merge)
            with self.assertRaises(ott.Error):
                ott.presets_favorites_command("unused.json", {"op": "restore", "preset": "Home", "path": "backup.json"},
                                             validate_document=validate, planner=planner, store_factory=mock.Mock(return_value=store))
            planner.assert_not_called()
            self.assertEqual((store.plans, store.applies), ([], []))


if __name__ == "__main__":
    unittest.main()
