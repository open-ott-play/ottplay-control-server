"""Real parser/entrypoint auto-routing; protocol/host have separate byte tests."""
import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ott_items_cli", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
commands = ott.favorites_module("global_favorites_items_commands")
LEGACY = json.loads((ROOT / "contracts/favorites-v1.json").read_text())
UUID = "00000000-0000-4000-8000-000000000001"


def caps(global_items=True):
    data = {"version": 1, "player": {"version": "1.2.0", "platform": "browser", "runtime": "page-123"},
            "lifecycle": [], "input": [], "playback": [],
            "favorites": {"version": 1, "operations": ["list", "add", "play", "remove"], "max_items": 100}}
    if global_items:
        data["favorites_global_items"] = {"version": 1}
    return data


class GlobalItemsCLITests(unittest.TestCase):
    def invoke(self, words, response, *, host_result=None):
        client = mock.Mock(timeout=45, server="https://fixture.invalid")
        client.device.return_value = "one"
        client.call.side_effect = response
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value={}), \
                mock.patch.object(ott, "Client", return_value=client), \
                mock.patch.object(commands, "run", side_effect=host_result) as host, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(["-j", "t1", *words])
        self.assertEqual(client.timeout, 45)
        return code, output.getvalue(), errors.getvalue(), client, host

    def test_aliases_force_namespaces_and_literal_r_numeric(self):
        for alias in ("fav", "favs", "favorite", "favorites", "favourite", "favourites"):
            self.assertEqual(ott.parse_command([alias, "global", "list"]),
                             ("favorites", {"op": "list", "_global": True}))
            self.assertEqual(ott.parse_command([alias, "global", "current"]),
                             ("favorites", {"op": "current", "_global": True}))
            self.assertEqual(ott.parse_command([alias, "legacy"]),
                             ("favorites", {"op": "list", "_legacy": True}))
            for value in ("R", "random", "1", "legacy", "global"):
                self.assertEqual(ott.parse_command([alias, "play", value]),
                                 ("favorites", {"op": "play", "query": value}))
        self.assertEqual(ott.parse_command(["fav", "current"]), ("favorites", {"op": "add"}))
        self.assertEqual(ott.parse_command(["fav", "rm", "id:" + UUID]), ("favorites", {"op": "remove", "id": UUID}))
        for words in (["legacy", "clear"], ["global", "list", "extra"], ["global", "current", "extra"]):
            with self.assertRaises(ott.Error):
                ott.parse_command(["fav", *words])

    def test_present_marker_routes_once_and_never_reaches_legacy_wire(self):
        result = {"collection": "global", "op": "add", "status": "verified", "verified": True}
        code, out, err, client, host = self.invoke(["fav", "add"], [caps()], host_result=[result])
        self.assertEqual((code, json.loads(out), err), (0, result, ""))
        self.assertEqual(client.call.call_args_list, [mock.call("one", "capabilities", {})])
        self.assertEqual(host.call_args.args[3], {"op": "add"})
        self.assertIsInstance(host.call_args.args[-1], float)
        self.assertEqual(host.call_count, 1)

    def test_every_global_error_or_unknown_retains_global_path(self):
        for failure in (ott.Error("safe error"), {"collection": "global", "op": "remove", "status": "unknown",
                                                  "operation_id": UUID, "verified": False}):
            code, out, err, client, host = self.invoke(["fav", "remove", UUID], [caps()], host_result=[failure])
            self.assertEqual((code, client.call.call_count, host.call_count), (1, 1, 1))
            self.assertNotIn("Legacy", err)
            if isinstance(failure, dict):
                self.assertEqual(json.loads(out)["operation_id"], UUID)

    def test_missing_marker_fallback_is_explicit_and_legacy_wire_unchanged(self):
        expected = LEGACY["list_result"]
        code, out, err, client, host = self.invoke(["fav"], [caps(False), expected])
        self.assertEqual((code, json.loads(out)), (0, expected))
        self.assertIn("does not advertise global", err)
        self.assertEqual(client.call.call_args_list[-1], mock.call("one", "favorites", {"op": "list", "runtime": "page-123"}))
        host.assert_not_called()
        code, _, err, client, host = self.invoke(["fav", "legacy"], [caps(), expected])
        self.assertEqual(code, 0)
        self.assertIn("explicitly selected", err)
        self.assertEqual(client.call.call_count, 2)
        host.assert_not_called()

    def test_global_only_selectors_and_invalid_marker_never_fall_back(self):
        malformed = caps()
        malformed["favorites_global_items"]["secret"] = "private-fixture-token"
        for words, data in ((["fav", "global", "list"], caps(False)),
                            (["fav", "play", "id:" + UUID], caps(False)),
                            (["fav", "play", UUID], caps(False)), (["fav"], malformed)):
            code, out, err, client, host = self.invoke(words, [data])
            self.assertEqual((code, out, client.call.call_count), (1, "", 1))
            self.assertNotIn("private-fixture-token", err)
            host.assert_not_called()

    def test_explicit_legacy_uuid_names_preserve_play_and_remove(self):
        for operation in ("play", "remove"):
            with self.subTest(operation=operation):
                expected = LEGACY[operation + "_result"]
                code, out, err, client, host = self.invoke(
                    ["fav", "legacy", operation, "--", UUID], [caps(), expected])
                self.assertEqual((code, json.loads(out)), (0, expected))
                self.assertIn("Legacy favorites explicitly selected", err)
                self.assertEqual(client.call.call_args_list[-1], mock.call("one", "favorites", {
                    "op": operation, "query": UUID, "runtime": "page-123"}))
                host.assert_not_called()
                code, out, _, client, host = self.invoke(
                    ["fav", "legacy", operation, "id:" + UUID], [caps()])
                self.assertEqual((code, out, client.call.call_count), (1, "", 1))
                host.assert_not_called()

    def test_same_revision_loader_and_marker_only_help(self):
        self.assertIs(commands.global_commands.storage.StorageError, ott.favorites_module("preset_global_favorites").StorageError)
        self.assertIs(commands.items.core, ott.favorites_module("global_favorites_protocol"))
        data = caps()
        data.pop("favorites")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_help({"capabilities": ott.capabilities_metadata(data)}, "t1")
        self.assertIn("fav global current", out.getvalue())
        self.assertNotIn("No controls", out.getvalue())
        self.assertNotIn("fav add", out.getvalue())

    def test_legacy_help_respects_read_only_capability_with_or_without_global_writes(self):
        for global_writes in (False, True):
            with self.subTest(global_writes=global_writes):
                data = caps(global_writes)
                data["favorites"]["operations"] = ["list", "play"]
                if global_writes:
                    data["favorites_global"] = {"version": 1}
                    data["favorites_global_recovery"] = {"version": 1}
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    ott.print_player_help({"capabilities": ott.capabilities_metadata(data)}, "t1")
                lines = out.getvalue().splitlines()
                self.assertEqual([line for line in lines if "fav legacy" in line], [
                    "  ott t1 fav legacy list / play ID_OR_NAME  — explicit legacy bookmarks"])
                self.assertEqual("ott t1 fav add [NAME]" in out.getvalue(), global_writes)

    def test_legacy_help_preserves_full_capability_commands(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_help({"capabilities": ott.capabilities_metadata(caps(False))}, "t1")
        self.assertEqual([line for line in out.getvalue().splitlines() if "fav legacy" in line], [
            "  ott t1 fav legacy list / add / play ID_OR_NAME / remove ID_OR_NAME  — explicit legacy bookmarks"])

    def test_mixed_group_targets_route_independently(self):
        config = {"players": {"t1": "one", "t2": "two"}}
        client = ott.Client.__new__(ott.Client)
        client.config, client.credentials = config, {"devices": [{"id": "one"}, {"id": "two"}]}
        client.timeout, client.server, client.on_receipt = 45, "https://fixture.invalid", None
        client.call = mock.Mock(side_effect=[caps(), caps(False), LEGACY["list_result"]])
        result = {"collection": "global", "op": "list", "status": "ready", "total": 0,
                  "entries": [], "unresolved_legacy_count": 0}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=config), mock.patch.object(ott, "Client", return_value=client), \
                mock.patch.object(commands, "run", return_value=result) as host, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(["-j", "t1,t2", "fav"])
        self.assertEqual((code, json.loads(out.getvalue())["succeeded"], host.call_count), (0, 2, 1))
        self.assertEqual(client.call.call_args_list, [mock.call("one", "capabilities", {}),
            mock.call("two", "capabilities", {}), mock.call("two", "favorites", {"op": "list", "runtime": "page-123"})])


if __name__ == "__main__":
    unittest.main()
