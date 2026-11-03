"""Real Favorites host/transport paths accept only the bounded queue extension."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
import global_favorites_commands as commands
import test_global_favorites_remote as fixture

spec = importlib.util.spec_from_file_location("queue_ack_ott", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
ABSENT = object()


class Controller(fixture.MemoryAPI):
    def __init__(self, clock, hint=ABSENT, bad_stage=None, extra=None):
        super().__init__(clock)
        self.hint, self.bad_stage, self.extra = hint, bad_stage, extra

    def api(self, path, payload=None, timeout=None):
        if payload["action"] == "capabilities":
            identifier = f"{len(self.responses) + 1:032x}"
            data = {"version": 1, "player": {"version": "0.2.0", "platform": "webos-native",
                    "runtime": "fixture-runtime"}, "lifecycle": [], "input": [], "playback": [],
                    "favorites_global": {"version": 1}}
            self.calls.append({"method": "POST", "op": "capabilities", "at": self.clock(), "timeout": timeout})
            self.responses[identifier] = ("capabilities", {"id": identifier, "status": "ok", "data": data})
            status, queued = 202, {"id": identifier, "status": "queued"}
        else:
            status, queued = super().api(path, payload, timeout)
        stage = "capabilities" if payload["action"] == "capabilities" else "global"
        if self.bad_stage is None or stage == self.bad_stage:
            if self.hint is not ABSENT:
                queued["receipt_wait_ms"] = self.hint
            if self.extra:
                queued.update(self.extra)
        return status, queued


class FavoritesQueueAckTests(unittest.TestCase):
    def invoke(self, client, clock, directory, op):
        path = directory / "cli.json"
        path.write_text("{}")
        params = {"op": op}
        if op == "export":
            params["path"] = str(directory / "favorites.json")
        return commands.run(ott, client, {}, path, [("fixture", "one")], params,
                            45, monotonic=clock, sleep=clock.sleep)

    def test_describe_and_export_accept_legacy_and_bounded_hint(self):
        for hint in (ABSENT, "1", "1000", 1, 1000):
            for op in ("describe", "export"):
                with self.subTest(hint=str(hint), op=op), tempfile.TemporaryDirectory() as name:
                    clock = fixture.Clock()
                    client = Controller(clock, hint)
                    result = self.invoke(client, clock, Path(name), op)
                    self.assertEqual(result["status"], "available" if op == "describe" else "exported")
                    self.assertEqual(client.posts(), ["capabilities", "describe"] + (["export"] if op == "export" else []))
                    self.assertTrue(all(call["timeout"] <= 45 for call in client.calls))
                    post_times = [c["at"] for c in client.calls if c["method"] == "POST"]
                    self.assertTrue(all(b - a >= 1.5 for a, b in zip(post_times, post_times[1:])))
                    if op == "export":
                        self.assertEqual(json.loads((Path(name) / "favorites.json").read_text()), {"version": 1, "entries": []})

    def test_bad_hint_stops_at_each_boundary_without_replay(self):
        for hint in (None, True, False, 0, 1001, -1, 1.0, "0", "01", "1001", " 1", "1.0", [], {}):
            for stage in ("capabilities", "global"):
                with self.subTest(hint=hint, stage=stage), tempfile.TemporaryDirectory() as name:
                    clock = fixture.Clock()
                    client = Controller(clock, hint, stage)
                    with self.assertRaises(ott.Error):
                        self.invoke(client, clock, Path(name), "describe")
                    self.assertEqual(client.posts(), ["capabilities"] + (["describe"] if stage == "global" else []))
                    gets = [c["op"] for c in client.calls if c["method"] == "GET"]
                    self.assertEqual(gets, ["capabilities"] if stage == "global" else [])

    def test_unknown_fields_and_invalid_required_fields_remain_rejected(self):
        for extra in ({"unknown": "private-do-not-echo"}, {"status": "pending"}, {"id": "bad-id"}, {"id": True}):
            for stage in ("capabilities", "global"):
                with self.subTest(extra=extra, stage=stage), tempfile.TemporaryDirectory() as name:
                    clock = fixture.Clock()
                    client = Controller(clock, "1000", stage, extra)
                    with self.assertRaises(ott.Error) as caught:
                        self.invoke(client, clock, Path(name), "describe")
                    self.assertNotIn("private-do-not-echo", str(caught.exception))
                    self.assertEqual(client.posts(), ["capabilities"] + (["describe"] if stage == "global" else []))


if __name__ == "__main__":
    unittest.main()
