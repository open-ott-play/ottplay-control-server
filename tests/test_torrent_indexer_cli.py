"""Indexer CLI contracts and secret-safe execution without a real network."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load("ott_indexer_test", ROOT / "cli/ott.py")
cli = load("torrent_indexer_test", ROOT / "cli/torrent_indexer.py")
RUNTIME = "indexer-runtime-1"
SECRET = "private-indexer-fixture-key"
ENDPOINT = "http://127.0.0.1:9696/1/api"
REF = "a" * 32


class Clock:
    value = 100.0

    def __call__(self):
        return self.value

    def sleep(self, duration):
        self.value += duration


class Client:
    def __init__(self, clock):
        self.clock, self.server, self.timeout = clock, "https://controller.invalid", 45
        self.calls, self.transform, self.before = [], None, None
        self.marker = {"version": 1}
        self.caps = {"sourceId": "home", "searchAvailable": True,
                     "categories": [{"id": 2000, "name": "Movies", "parentId": None}], "maxLimit": 100}
        self.item = {"resultRef": REF, "title": "Big Buck Bunny", "size": 123, "seeders": None,
                     "publishedAt": "2008-04-10T00:00:00Z", "categoryIds": [2000], "languages": ["en"]}

    def call(self, device, action, request):
        self.calls.append((action, copy.deepcopy(request), self.timeout))
        if self.before:
            self.before(action, request)
        if action == "capabilities":
            value = {"version": 1, "player": {"runtime": RUNTIME, "platform": "tauri", "version": "1.1.62"},
                     "lifecycle": [], "input": [], "playback": []}
            if self.marker is not None:
                value["torrent_indexer"] = self.marker
            return value
        assert action == "torrent_indexer"
        op = request["op"]
        if op == "list":
            result = {"sources": [{"id": "home", "label": "Local indexer"}]}
        elif op == "configure":
            result = {"source": {"id": request["sourceId"], "label": request["label"]}}
        elif op == "remove":
            result = {"removed": True}
        elif op == "caps":
            result = self.caps
        elif op == "search":
            result = {"sourceId": "home", "items": [self.item], "nextOffset": request["offset"] + 1, "total": None}
        elif op == "prepare":
            result = {"sourceId": "home", "sourceRef": "b" * 32}
        else:
            raise AssertionError(op)
        value = {"version": 1, "runtime": RUNTIME, "op": op, "schema": 1, **copy.deepcopy(result)}
        return self.transform(request, value) if self.transform else value


class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.clock = Clock()
        self.client = Client(self.clock)
        self.key_path = self.directory / "key"
        self.key_path.write_text("sample-input\n")
        self.key_path.chmod(0o600)

    def run_cli(self, words, *, timeout=45, json_output=True):
        output, errors = io.StringIO(), io.StringIO()
        factory = cli.Session
        with mock.patch.object(cli, "Session", side_effect=lambda *a, **kw:
                               factory(*a, **kw, clock=self.clock, sleep=self.clock.sleep)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.run(ott, self.client, "device-1", words, timeout, json_output, self.directory / "cli.json")
        raw = output.getvalue()
        self.assertEqual(errors.getvalue(), "")
        self.assertNotIn(SECRET, raw)
        self.assertNotIn("sample-input", raw)
        self.assertNotIn(ENDPOINT, raw)
        return code, json.loads(raw) if json_output else raw

    def configure(self, *extra):
        return ["configure", "home", "--label", "Local indexer", "--endpoint", ENDPOINT,
                "--api-key-file", str(self.key_path), *extra]

    def operations(self):
        return [params["op"] for action, params, _ in self.client.calls if action == "torrent_indexer"]

    def test_all_operations_and_opaque_prepared_source(self):
        for words in (["list"], self.configure(), ["remove", "home"], ["caps", "home"],
                      ["search", "home", "Big", "Buck", "Bunny", "--category", "2000", "--offset", "4", "--limit", "10"],
                      ["prepare", "home", REF]):
            with self.subTest(words=words[:2]):
                code, value = self.run_cli(words)
                self.assertEqual((code, value["status"]), (0, "ok"))
        self.assertEqual(value["source"], "ottplay-local-torrent:home/" + "b" * 32)
        requests = [r for a, r, _ in self.client.calls if a == "torrent_indexer"]
        configured = next(r for r in requests if r["op"] == "configure")
        self.assertEqual(configured["apiKey"], "sample-input")
        self.assertNotIn("key_file", configured)
        search = next(r for r in requests if r["op"] == "search")
        self.assertEqual((search["query"], search["offset"], search["limit"]), ("Big Buck Bunny", 4, 10))
        self.assertEqual(self.operations().count("search"), 1)
        self.assertEqual(self.client.timeout, 45)

    def test_preflight_invalid_arguments_never_calls_player(self):
        invalid = [["configure", "home", "--api-key", SECRET], ["list", "extra"],
                   ["remove", "../home"], ["caps", "HOME"], ["prepare", "home", "A" * 32],
                   ["search", "home", "--limit", "0"], ["search", "home", "--offset", "1000001"],
                   ["search", "home", "--category", "0"], ["search", "home", "--category", "2,2"],
                   ["search", "home", "query", "--query", "duplicate"],
                   ["search", "home", "é" * 257], self.configure("--api-key-env", "KEY")]
        for words in invalid:
            with self.subTest(words=words[:2]):
                self.assertEqual(self.run_cli(words)[0], 1)
        self.assertEqual(self.client.calls, [])

    def test_endpoint_rejects_credentials_delimiters_and_controls(self):
        for value in ["http://user:pass@127.0.0.1/api", ENDPOINT + "?", ENDPOINT + "#", ENDPOINT + "?key=x",
                      "file:///private/file", "http://127.0.0.1:0/api", "http://127.0.0.1:65536/api",
                      "http://127.0.0.1/\\api", ENDPOINT + "\n"]:
            with self.subTest(value=value):
                words = self.configure()
                words[words.index("--endpoint") + 1] = value
                self.assertEqual(self.run_cli(words)[0], 1)
        self.assertEqual(self.client.calls, [])

    def test_missing_invalid_and_nonregular_key_files_fail_locally(self):
        for value in [self.directory / "missing", self.directory]:
            words = self.configure()
            words[-1] = str(value)
            self.assertEqual(self.run_cli(words)[0], 1)
        for raw in [b"bad\xff", b"a" * 2049, b"one\ntwo"]:
            self.key_path.write_bytes(raw)
            self.assertEqual(self.run_cli(self.configure())[0], 1)
        if os.name != "nt":
            fifo = self.directory / "fifo"
            os.mkfifo(fifo)
            words = self.configure()
            words[-1] = str(fifo)
            self.assertEqual(self.run_cli(words)[0], 1)
        self.assertEqual(self.client.calls, [])

    def test_empty_keys_and_environment_input(self):
        self.key_path.write_text("")
        self.assertEqual(self.run_cli(self.configure())[0], 0)
        self.assertEqual(self.client.calls[-1][1]["apiKey"], "")
        words = self.configure()[:-2] + ["--api-key-env", "OTT_INDEXER_TEST_KEY"]
        with mock.patch.dict(os.environ, {"OTT_INDEXER_TEST_KEY": SECRET}):
            self.assertEqual(self.run_cli(words)[0], 0)
        self.assertEqual(self.client.calls[-1][1]["apiKey"], SECRET)
        with mock.patch.dict(os.environ, {}, clear=True):
            before = len(self.client.calls)
            self.assertEqual(self.run_cli(words)[0], 1)
            self.assertEqual(len(self.client.calls), before)

    def test_unadvertised_or_malformed_capability_blocks_action(self):
        for marker in [None, {}, {"version": True}, {"version": 2}, {"version": 1, "extra": SECRET}]:
            self.client.marker = marker
            self.assertEqual(self.run_cli(["remove", "home"])[0], 1)
        self.assertEqual(self.operations(), [])

    def test_caps_fence_search_categories_and_page_size(self):
        self.assertEqual(self.run_cli(["search", "home", "--category", "999"])[0], 1)
        self.client.caps["maxLimit"] = 20
        self.assertEqual(self.run_cli(["search", "home"])[0], 1)
        self.client.caps["searchAvailable"] = False
        self.assertEqual(self.run_cli(["search", "home", "query", "--limit", "10"])[0], 1)
        self.assertEqual(self.run_cli(["search", "home", "--limit", "10"])[0], 1)
        self.assertEqual(self.operations(), ["caps", "caps", "caps", "caps"])

    def test_mutations_are_not_retried_on_transport_failure(self):
        def fail(action, request):
            if action == "torrent_indexer":
                raise RuntimeError(SECRET + ENDPOINT)
        self.client.before = fail
        for words in [self.configure(), ["remove", "home"], ["prepare", "home", REF]]:
            with self.subTest(op=words[0]):
                before = len(self.operations())
                code, value = self.run_cli(words)
                self.assertEqual((code, value["status"], value["replay_allowed"]), (2, "unknown", False))
                self.assertEqual(len(self.operations()), before + 1)

    def test_valid_rejection_is_definitive_and_malformed_rejection_is_safe(self):
        def fail(request, result):
            raise ott.PlayerRejected(SECRET, {"version": 1, "runtime": RUNTIME, "op": request["op"],
                "error": "Torrent indexer request failed.", "reason": "torznab_source_missing"})
        self.client.transform = fail
        code, value = self.run_cli(["prepare", "home", REF])
        self.assertEqual((code, value["status"], value["reason"]), (1, "failed", "torznab_source_missing"))
        self.client.transform = lambda request, result: (_ for _ in ()).throw(
            ott.PlayerRejected(SECRET, {"error": SECRET, "endpoint": ENDPOINT}))
        code, value = self.run_cli(self.configure())
        self.assertEqual((code, value["status"]), (2, "unknown"))

    def test_runtime_op_source_and_unknown_fields_are_rejected(self):
        for field, injected in [("runtime", "other"), ("op", "list"), ("sourceId", "other"),
                                ("apiKey", SECRET), ("endpoint", ENDPOINT), ("version", True)]:
            self.client.transform = lambda req, value, k=field, v=injected: {**value, k: v}
            code, value = self.run_cli(["prepare", "home", REF])
            self.assertEqual((code, value["status"]), (2, "unknown"))
        self.assertEqual(self.operations(), ["prepare"] * 6)

    def test_deadline_and_server_drift_prevent_next_rpc(self):
        self.client.before = lambda action, request: setattr(self.clock, "value", self.clock() + 60)
        self.assertEqual(self.run_cli(["prepare", "home", REF])[0], 1)
        self.assertEqual(self.operations(), [])
        self.client.before = lambda action, request: setattr(self.client, "server", "https://other.invalid")
        self.assertEqual(self.run_cli(["prepare", "home", REF])[0], 1)
        self.assertEqual(self.operations(), [])

    def test_output_has_readable_results_and_no_private_label(self):
        self.assertIn("Big Buck Bunny", self.run_cli(["search", "home"], json_output=False)[1])
        self.client.item["title"] = "Open " + ENDPOINT
        self.assertIn("[private text]", self.run_cli(["search", "home"], json_output=False)[1])
        words = self.configure()[:-2] + ["--api-key-env", "OTT_INDEXER_TEST_KEY"]
        words[words.index("--label") + 1] = SECRET
        with mock.patch.dict(os.environ, {"OTT_INDEXER_TEST_KEY": SECRET}):
            self.assertIn("[private text]", self.run_cli(words, json_output=False)[1])
        self.assertIn("Playback has not started", self.run_cli(["prepare", "home", REF], json_output=False)[1])

    def test_shared_wire_fixtures_match_cli_projection(self):
        fixture = json.loads((ROOT / "contracts/torrent-indexer-v1.json").read_text())
        for row in fixture["valid"]:
            with self.subTest(name=row["name"]):
                if row["status"] == "ok":
                    self.assertEqual(cli.validate_response(row["data"], row["params"]), row["data"])
                else:
                    with self.assertRaises(cli.IndexerError) as caught:
                        cli.validate_response(row["data"], row["params"])
                    self.assertTrue(caught.exception.rejected)
        for row in fixture["invalid_results"]:
            with self.subTest(name=row["name"]):
                with self.assertRaises((cli.IndexerError, TypeError)):
                    cli.validate_response(row["data"], row["params"])

    def test_null_metadata_and_utf8_and_collection_limits(self):
        for key in ("size", "seeders", "publishedAt"):
            self.client.item[key] = None
        self.assertEqual(self.run_cli(["search", "home"])[0], 0)
        for key, value in [("size", True), ("seeders", -1), ("publishedAt", "yesterday"),
                           ("title", "é" * 513), ("languages", ["not a language"]),
                           ("languages", ["en"] * 17), ("categoryIds", [0])]:
            previous = self.client.item[key]
            self.client.item[key] = value
            self.assertEqual(self.run_cli(["search", "home"])[0], 1)
            self.client.item[key] = previous

    def test_configuration_key_spaces_are_preserved(self):
        self.key_path.write_text(" key with spaces \n")
        self.assertEqual(self.run_cli(self.configure())[0], 0)
        self.assertEqual(self.client.calls[-1][1]["apiKey"], " key with spaces ")
        words = self.configure()[:-2] + ["--api-key-env", "OTT_INDEXER_TEST_KEY"]
        with mock.patch.dict(os.environ, {"OTT_INDEXER_TEST_KEY": "bad\n"}):
            count = len(self.client.calls)
            self.assertEqual(self.run_cli(words)[0], 1)
            self.assertEqual(len(self.client.calls), count)


    def test_maximum_escaped_page_and_strict_timestamp_offsets(self):
        request = {"version": 1, "runtime": RUNTIME, "op": "search", "sourceId": "home",
                   "query": "", "categoryIds": [], "offset": 0, "limit": 100}
        item = {**self.client.item, "title": '"' * 1024,
                "categoryIds": list(range(cli.MAX_SAFE - 31, cli.MAX_SAFE + 1)),
                "languages": ["a" * 32 for _ in range(16)]}
        result = {"version": 1, "runtime": RUNTIME, "op": "search", "schema": 1, "sourceId": "home",
                  "items": [{**item, "resultRef": f"{index:032x}"} for index in range(100)],
                  "nextOffset": None, "total": 100}
        self.assertGreater(len(cli._canonical(result)), 256 * 1024)
        self.assertEqual(cli.validate_response(result, request), result)
        for stamp in ["2026-10-30T00:00:00+24:00", "2026-10-30T00:00:00+01:60", "2026-02-30T00:00:00Z"]:
            self.client.item["publishedAt"] = stamp
            self.assertEqual(self.run_cli(["search", "home"])[0], 1)
        self.client.item["publishedAt"] = "2026-10-30t00:00:00z"
        self.assertEqual(self.run_cli(["search", "home"])[0], 0)


if __name__ == "__main__":
    unittest.main()
