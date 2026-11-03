"""Child indexers require explicit support and never lose their selected scope."""
import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "cli" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load("ott_indexer_children_tests", "ott.py")
indexer = load("indexer_children_tests", "torrent_indexer.py")
RUNTIME = "children-test-runtime"
MARKER = {"version": 1, "operations": ["indexers"], "scopedOperations": ["caps", "search"]}
PRIVATE = "http://private.invalid/key"


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Client:
    def __init__(self):
        self.clock = Clock()
        self.server, self.timeout = "https://controller.invalid", 45
        self.marker = copy.deepcopy(MARKER)
        self.calls, self.transform = [], None
        self.supported = True
        self.rows = [{"id": "007", "label": "Numeric ID", "language": None, "type": None},
                     {"id": "UpperCase_Tracker", "label": "Uppercase ID", "language": "en-US", "type": "public"}]

    def call(self, device, action, request):
        self.calls.append((action, copy.deepcopy(request)))
        if action == "capabilities":
            result = {"version": 1, "player": {"runtime": RUNTIME, "platform": "tauri", "version": "test"},
                      "lifecycle": [], "input": [], "playback": [], "torrent_indexer": {"version": 1}}
            if self.marker is not None:
                result["torrent_indexer_children"] = copy.deepcopy(self.marker)
            return result
        if action != "torrent_indexer":
            raise AssertionError("unexpected action")
        result = {"version": 1, "runtime": RUNTIME, "op": request["op"], "schema": 1,
                  "sourceId": request["sourceId"]}
        if "indexerId" in request:
            result["indexerId"] = request["indexerId"]
        if request["op"] == "indexers":
            result.update(supported=self.supported, indexers=copy.deepcopy(self.rows))
        elif request["op"] == "caps":
            result.update(searchAvailable=True, categories=[], maxLimit=100)
        elif request["op"] == "search":
            result.update(items=[], nextOffset=None, total=None)
        else:
            raise AssertionError("child discovery must not mutate or start playback")
        return self.transform(request, result) if self.transform else result


class ChildIndexerTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()

    def execute(self, words):
        session = indexer.Session
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(indexer, "Session", side_effect=lambda api, client, *args, **kwargs:
                               session(api, client, *args, clock=client.clock,
                                       sleep=client.clock.sleep, **kwargs)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = indexer.run(ott, self.client, "dev1", words, 45, True)
        self.assertNotIn(PRIVATE, output.getvalue() + errors.getvalue())
        self.assertEqual(self.client.timeout, 45)
        return code, json.loads(output.getvalue())

    def requests(self):
        return [request for action, request in self.client.calls if action == "torrent_indexer"]

    def test_shared_controller_fixtures_have_identical_python_response_verdicts(self):
        fixture = json.loads((ROOT / "contracts" / "torrent-indexer-children-v1.json").read_text())
        self.assertTrue(fixture["valid"])
        self.assertTrue(fixture["invalid_results"])
        for row in fixture["valid"]:
            with self.subTest(name=row["name"]):
                if row["status"] == "ok":
                    self.assertEqual(indexer.validate_response(row["data"], row["params"]), row["data"])
                else:
                    with self.assertRaises(indexer.IndexerError) as caught:
                        indexer.validate_response(row["data"], row["params"])
                    self.assertTrue(caught.exception.rejected)
                    self.assertEqual(caught.exception.code, row["data"]["reason"])
        for row in fixture["invalid_results"]:
            with self.subTest(name=row["name"]), self.assertRaises(indexer.IndexerError):
                indexer.validate_response(row["data"], row["params"])

    def test_discovery_preserves_numeric_uppercase_ids_and_nullable_metadata(self):
        code, result = self.execute(["indexers", "home"])
        self.assertEqual((code, result["status"], result["supported"]), (0, "ok", True))
        self.assertEqual(result["indexers"], self.client.rows)
        self.assertEqual(self.requests(), [{"version": 1, "runtime": RUNTIME,
                                            "op": "indexers", "sourceId": "home"}])

    def test_absent_or_invalid_marker_blocks_every_scoped_operation_before_indexer_rpc(self):
        invalid = [None, {"version": True, "operations": ["indexers"], "scopedOperations": ["caps", "search"]},
                   {**MARKER, "version": 2}, {**MARKER, "operations": ["indexers", "prepare"]},
                   {**MARKER, "scopedOperations": ["search"]}, {**MARKER, "extra": PRIVATE}]
        commands = [["indexers", "home"], ["caps", "home", "--indexer", "007"],
                    ["search", "home", "movie", "--indexer", "007"],
                    ["doctor", "home", "--indexer", "007", "--query", "movie"]]
        for marker in invalid:
            for words in commands:
                with self.subTest(marker=marker, operation=words[0]):
                    self.client = Client()
                    self.client.marker = marker
                    code, result = self.execute(words)
                    self.assertEqual(code, 1)
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(self.requests(), [])

    def test_marker_scope_order_is_not_significant(self):
        self.client.marker["scopedOperations"].reverse()
        self.assertEqual(self.execute(["caps", "home", "--indexer", "007"])[0], 0)

    def test_selected_caps_and_search_use_the_exact_same_case_sensitive_id(self):
        for identifier in ("007", "UpperCase_Tracker", "X" * 128):
            with self.subTest(identifier=identifier):
                self.client = Client()
                code, result = self.execute(["search", "home", "movie", "--indexer", identifier, "--limit", "7"])
                self.assertEqual((code, result["indexerId"]), (0, identifier))
                self.assertEqual([row["op"] for row in self.requests()], ["caps", "search"])
                self.assertEqual([row["indexerId"] for row in self.requests()], [identifier, identifier])
                self.assertEqual(self.requests()[-1]["limit"], 7)

    def test_scoped_doctor_does_not_search_implicitly_and_keeps_query_small(self):
        for query in ([], ["--query", "movie"]):
            with self.subTest(query=bool(query)):
                self.client = Client()
                code, result = self.execute(["diagnose", "home", "--indexer", "UpperCase_Tracker", *query])
                self.assertEqual((code, result["indexerId"], result["search_tested"]),
                                 (0, "UpperCase_Tracker", bool(query)))
                self.assertEqual([row["op"] for row in self.requests()], ["caps", "search"] if query else ["caps"])
                self.assertTrue(all(row["indexerId"] == "UpperCase_Tracker" for row in self.requests()))
                if query:
                    self.assertEqual(self.requests()[-1]["limit"], 5)

    def test_invalid_or_aggregate_ids_are_rejected_before_any_player_call(self):
        for identifier in ("all", "All", "ALL", "_bad", "bad/id", "a?b", "a b", "кино", "A" * 129, ""):
            for command in ("caps", "search", "doctor"):
                with self.subTest(identifier=identifier, operation=command):
                    self.client = Client()
                    self.assertEqual(self.execute([command, "home", "--indexer", identifier])[0], 1)
                    self.assertEqual(self.client.calls, [])

    def test_scoped_reply_must_echo_selection_and_cannot_add_unknown_fields(self):
        for failing_op in ("caps", "search"):
            for mismatch in ("missing", "wrong_case", "unknown_field"):
                with self.subTest(operation=failing_op, mismatch=mismatch):
                    self.client = Client()

                    def transform(request, result):
                        if request["op"] == failing_op:
                            if mismatch == "missing":
                                result.pop("indexerId")
                            elif mismatch == "wrong_case":
                                result["indexerId"] = "upper"
                            else:
                                result["endpoint"] = PRIVATE
                        return result

                    self.client.transform = transform
                    code, result = self.execute(["search", "home", "movie", "--indexer", "Upper"])
                    self.assertEqual((code, result["reason"]), (1, "invalid_response"))
                    self.assertEqual([row["op"] for row in self.requests()],
                                     ["caps"] if failing_op == "caps" else ["caps", "search"])

    def test_legacy_queries_keep_old_shape_and_reject_an_unsolicited_child_scope(self):
        self.client.marker = None
        code, result = self.execute(["search", "home", "movie"])
        self.assertEqual(code, 0)
        self.assertEqual(set(result), {"status", "version", "runtime", "op", "schema", "sourceId",
                                       "items", "nextOffset", "total"})
        self.assertTrue(all("indexerId" not in row for row in self.requests()))
        self.client = Client()
        self.client.transform = lambda request, value: {**value, "indexerId": "007"}
        self.assertEqual(self.execute(["search", "home", "movie"])[0], 1)
        self.assertEqual([row["op"] for row in self.requests()], ["caps"])

    def test_discovery_bounds_uniqueness_and_unsupported_shape(self):
        self.client.rows = [{"id": str(number), "label": "Child", "language": None, "type": None}
                            for number in range(256)]
        self.assertEqual(len(self.execute(["indexers", "home"])[1]["indexers"]), 256)
        good = copy.deepcopy(self.client.rows)
        for rows, supported in ((good + [good[0]], True), ([good[0], good[0]], True),
                                ([good[0]], False), ([{**good[0], "id": "All"}], True),
                                ([{**good[0], "endpoint": PRIVATE}], True)):
            with self.subTest(count=len(rows), supported=supported):
                self.client = Client()
                self.client.rows, self.client.supported = rows, supported
                self.assertEqual(self.execute(["indexers", "home"])[0], 1)
        self.client = Client()
        self.client.rows, self.client.supported = [], False
        code, result = self.execute(["indexers", "home"])
        self.assertEqual((code, result["supported"], result["indexers"]), (0, False, []))

    def test_discovery_text_bounds_are_utf8_bytes_and_private_fields_are_redacted(self):
        row = {"id": "007", "label": "é" * 64, "language": "é" * 16, "type": "x" * 32}
        self.client.rows = [row]
        self.assertEqual(self.execute(["indexers", "home"])[0], 0)
        for field, value in (("label", "é" * 65), ("language", "é" * 17), ("type", "x" * 33)):
            with self.subTest(field=field):
                self.client = Client()
                self.client.rows = [{**row, field: value}]
                self.assertEqual(self.execute(["indexers", "home"])[0], 1)
        self.client = Client()
        self.client.rows = [{"id": "007", "label": PRIVATE, "language": "~/secret", "type": "magnet:private"}]
        code, result = self.execute(["indexers", "home"])
        self.assertEqual(code, 0)
        self.assertEqual(result["indexers"], [{"id": "007", "label": "[private text]",
                                              "language": "[private text]", "type": "[private text]"}])


if __name__ == "__main__":
    unittest.main()
