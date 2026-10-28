"""Ordinary global items use real paged reads and the existing transfer pipeline."""
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load("ott_items_host", ROOT / "cli/ott.py")
commands = ott.favorites_module("global_favorites_items_commands")
fixture = load("items_core_fixture", ROOT / "tests/test_global_favorites_remote.py")
RECOVERY = json.loads((ROOT / "docs/design/global-favorites-recovery-v1.fixtures.json").read_bytes())["positive"]
RUNTIME = "fixture-runtime"
ID = "00000000-0000-4000-8000-000000000001"


class MemoryAPI(fixture.MemoryAPI):
    def __init__(self, clock):
        super().__init__(clock)
        self.actions = []
        self.now_entry = fixture.row(2)
        self.item_error = None
        self.blocked = False
        self.item_context = "2" * 64

    def api(self, path, payload=None, timeout=None):
        action, params = payload["action"], payload["params"]
        self.actions.append((action, params["op"]))
        if action == "favorites_global":
            return super().api(path, payload, timeout)
        request_id = f"{len(self.responses) + 1:032x}"
        op = params["op"]
        common = {"version": 1, "runtime": params["runtime"], "op": op, "subject": "global"}
        if action == "favorites_global_items":
            commands.items.validate_request(params)
            data = {**common, "revision": fixture.digest(commands.document.canonical_bytes(self.current)),
                    "context_revision": self.item_context}
            if op == "current":
                data.update(entry=copy.deepcopy(self.now_entry),
                            unavailable_reason=None if self.now_entry is not None else "standby")
            elif op == "list":
                rows = [{"id": f"{i:08x}-0000-4000-8000-000000000001", "entry": row}
                        for i, row in enumerate(self.current["entries"])]
                data.update(cursor=None, page_offset=0, total=len(rows), entries=rows,
                            next_cursor=None, unresolved_legacy_count=2)
            else:
                data.update(id=params["id"], dispatched=True)
            if self.item_error:
                data = {**common, "error": self.item_error}
        else:
            self.assert_recovery(action, params)
            data = copy.deepcopy(RECOVERY["empty_page_response"])
            data["runtime"] = params["runtime"]
            if self.blocked:
                raise commands.core.RemoteError("busy")
        self.responses[request_id] = (op, {"id": request_id, "status": "rejected" if "error" in data else "ok", "data": data})
        self.calls.append({"method": "POST", "op": op, "at": self.clock(), "timeout": timeout})
        return 202, {"id": request_id, "status": "queued"}

    @staticmethod
    def assert_recovery(action, params):
        assert action == "favorites_global_recovery" and params["op"] == "inspect" and "cursor" in params


class GlobalItemsCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cli.json"
        self.path.parent.chmod(0o700)
        self.clock = fixture.Clock()
        self.client = MemoryAPI(self.clock)
        self.controls = {"player": {"runtime": RUNTIME}, "favorites_global_items": {"version": 1},
                         "favorites_global": {"version": 1}, "favorites_global_recovery": {"version": 1}}
        original = commands.global_commands._Durable.save
        def save(durable, record):
            value = original(durable, record)
            self.client.events.append("saved")
            return value
        patch = mock.patch.object(commands.global_commands._Durable, "save", save)
        patch.start()
        self.addCleanup(patch.stop)

    def run_command(self, params, budget=45):
        return commands.run(ott, self.client, "one", params, self.controls, self.path,
                            self.clock() + budget, monotonic=self.clock, sleep=self.clock.sleep)

    def test_add_reads_current_then_real_core_with_durable_intent_and_one_commit(self):
        self.client.current = fixture.doc(fixture.row(1))
        result = self.run_command({"op": "add", "name": "New title"})
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.client.current, fixture.doc(fixture.row(1), fixture.row(2, title="New title")))
        self.assertEqual(self.client.actions, [("favorites_global_items", "current"),
            ("favorites_global", "describe"), ("favorites_global", "export"),
            ("favorites_global_recovery", "inspect"), ("favorites_global", "describe"),
            ("favorites_global", "begin"), ("favorites_global", "append"),
            ("favorites_global", "preview"), ("favorites_global", "commit")])
        self.assertEqual(len(list(self.path.parent.glob(".favorites-global-operations-*"))), 1)
        times = [row["at"] for row in self.client.calls if row["method"] == "POST"]
        self.assertTrue(all(b - a >= 1.5 for a, b in zip(times, times[1:])))
        self.assertLess(self.clock(), 145)

    def test_remove_uuid_preserves_other_entries_via_replace(self):
        self.client.current = fixture.doc(fixture.row(1), fixture.row(2))
        result = self.run_command({"op": "remove", "id": ID})
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.client.current, fixture.doc(fixture.row(2)))
        self.assertEqual(self.client.begin["mode"], "replace")
        self.assertEqual(self.client.actions.count(("favorites_global", "commit")), 1)

    def test_title_conflict_is_actionable_without_staging_or_saving(self):
        self.client.current = fixture.doc(fixture.row(2))
        result = self.run_command({"op": "add", "name": "Different"})
        self.assertEqual((result["status"], result["reason"]), ("blocked", "metadata_conflict"))
        self.assertEqual(result["title_conflicts"][0]["incoming_title"], "Different")
        self.assertEqual(self.client.posts(), ["current", "describe", "export"])
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_revision_context_and_exact_document_changes_never_begin(self):
        for change in (lambda data: {**data, "context_revision": "9" * 64},
                       lambda data: {**data, "revision": "9" * 64}):
            with self.subTest(change=change):
                self.clock = fixture.Clock()
                self.client = MemoryAPI(self.clock)
                self.client.current = fixture.doc(fixture.row(1))
                self.client.mutators["describe"] = lambda data, _: change(data)
                with self.assertRaises(ott.Error):
                    self.run_command({"op": "remove", "id": ID})
                self.assertNotIn("begin", self.client.posts())
        self.clock = fixture.Clock()
        self.client = MemoryAPI(self.clock)
        self.client.current = fixture.doc(fixture.row(1))
        with mock.patch.object(commands.items.Listing, "document", property(
                lambda value: fixture.doc(fixture.row(1, title="Changed")))):
            with self.assertRaises(ott.Error):
                self.run_command({"op": "remove", "id": ID})
        self.assertNotIn("begin", self.client.posts())

    def test_list_play_current_readonly_and_literal_queries(self):
        self.client.current = fixture.doc(fixture.row(1, title="R"), fixture.row(2, title="1"))
        for name in ("R", "1"):
            result = self.run_command({"op": "play", "query": name})
            self.assertEqual((result["status"], result["entry"]["title"]), ("dispatched", name))
        result = self.run_command({"op": "list"})
        out = io.StringIO()
        with redirect_stdout(out):
            commands.print_result(ott, result)
        self.assertIn("Legacy-only favorites: 2", out.getvalue())
        self.assertEqual(self.run_command({"op": "current"})["status"], "ready")
        self.assertTrue(all(action == "favorites_global_items" for action, _ in self.client.actions))
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_no_guessing_ambiguous_names_legacy_ids_or_missing_current(self):
        self.client.current = fixture.doc(fixture.row(1, title="Same"), fixture.row(2, title="Same"))
        for params in ({"op": "play", "query": "Same"}, {"op": "remove", "query": "Sam"},
                       {"op": "play", "id": "fav-abc123"}):
            with self.assertRaises(ott.Error):
                self.run_command(params)
        self.client.now_entry = None
        with self.assertRaises(ott.Error):
            self.run_command({"op": "add"})
        self.assertTrue(all(action == "favorites_global_items" for action, _ in self.client.actions))

    def test_deadline_and_recovery_failure_block_staging(self):
        with self.assertRaises(ott.Error):
            self.run_command({"op": "add"}, budget=1)
        self.assertEqual(self.client.actions, [])
        self.client.blocked = True
        with self.assertRaises(ott.Error):
            self.run_command({"op": "add"})
        self.assertNotIn("begin", self.client.posts())
        self.assertEqual(list(self.path.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
