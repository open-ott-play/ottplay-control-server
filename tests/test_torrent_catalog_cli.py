"""Real Torrent CLI against a bounded fake controller; no network or devices."""
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ott = load("ott_torrent_test", ROOT / "cli/ott.py")
cli = load("torrent_test", ROOT / "cli/torrent_catalog.py")
doc = cli.doc
RUNTIME = "torrent-runtime-1"
SOURCE = "https://example.invalid/private-source.torrent?token=private-token"


def catalog():
    return {"schema": 1, "id": "catalog_1", "name": "Home", "entries": [
        {"id": "entry_1", "title": "First", "category": "First", "source": SOURCE,
         "files": [{"index": 3, "name": "episode.mkv", "length": 1234},
                   {"index": 7, "name": "next.MP4", "length": 5678}]}]}


class Clock:
    value = 100.0

    def __call__(self):
        return self.value

    def sleep(self, delay):
        self.value += delay


class Client:
    """Independent wire fixture with operation state, chunk progress and CAS."""
    def __init__(self, clock, value):
        self.clock, self.current = clock, copy.deepcopy(value)
        self.server, self.timeout = "https://controller.invalid", 45
        self.calls, self.snapshots, self.operations, self.uploads = [], {}, {}, {}
        self.counter, self.revision, self.writes, self.plays = 0, "rev:1", 0, 0
        self.marker, self.transform, self.before = {"version": 1}, None, None
        self.extra_caps = {}
        self.metadata_files = copy.deepcopy(catalog()["entries"][0]["files"])
        self.pending, self.drift = False, False

    def api(self, *args, **kwargs):
        raise AssertionError("Unexpected HTTP path in in-memory fixture")

    api_raw = api

    def snapshot(self, value, kind="catalog"):
        self.counter += 1
        identifier = f"00000000-0000-4000-8000-{self.counter:012d}"
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self.snapshots[identifier] = raw
        return {"id": identifier, "kind": kind, "revision": self.revision if kind == "catalog" else None,
                "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}

    def op(self, identifier, kind, state, **kw):
        value = {"id": identifier, "kind": kind, "state": state, "next_offset": None,
                 "snapshot": None, "reason": None, **kw}
        self.operations[identifier] = value
        return {"operation": copy.deepcopy(value)}

    def call(self, device, action, request):
        self.calls.append((action, copy.deepcopy(request), self.clock(), self.timeout))
        if self.before:
            self.before(action, request)
        if action == "capabilities":
            result = {"version": 1, "player": {"runtime": RUNTIME, "platform": "tauri", "version": "1.1.62"},
                      "lifecycle": [], "input": [], "playback": []}
            if self.marker is not None:
                result["torrent_catalog"] = self.marker
            result.update(self.extra_caps)
            return result
        assert action == "torrent_catalog"
        op, identifier = request["op"], request.get("operation_id")
        response = {"version": 1, "op": op, "runtime": RUNTIME}
        if op == "read":
            result = {"snapshot": self.snapshot(self.current)}
        elif op == "read_chunk":
            raw, offset = self.snapshots[request["snapshot_id"]], request["offset"]
            part = raw[offset:offset + request["max_bytes"]]
            result = {"snapshot_id": request["snapshot_id"], "offset": offset,
                      "data_b64": base64.b64encode(part).decode(),
                      "next_offset": offset + len(part) if offset + len(part) < len(raw) else None}
        elif op == "metadata":
            value = {"schema": 1, "source": request["source"], "files": self.metadata_files}
            result = self.op(identifier, "metadata", "pending" if self.pending else "ready",
                             snapshot=None if self.pending else self.snapshot(value, "metadata"))
            if self.drift:
                self.revision = "rev:changed"
        elif op == "status":
            result = {"operation": copy.deepcopy(self.operations[identifier])}
        elif op == "cancel":
            result = self.op(identifier, self.operations[identifier]["kind"], "cancelled")
        elif op == "write_begin":
            self.uploads[identifier] = [copy.deepcopy(request), bytearray()]
            result = self.op(identifier, "write", "uploading", next_offset=0)
        elif op == "write_chunk":
            original, raw = self.uploads[identifier]
            assert request["offset"] == len(raw)
            raw.extend(base64.b64decode(request["data_b64"], validate=True))
            result = self.op(identifier, "write", "uploading", next_offset=len(raw))
        elif op == "write_commit":
            original, raw = self.uploads[identifier]
            assert len(raw) == original["size_bytes"] and hashlib.sha256(raw).hexdigest() == original["sha256"]
            if original["revision"] != self.revision:
                result = self.op(identifier, "write", "failed", reason="revision_conflict")
            else:
                incoming = json.loads(raw)
                if original["mode"] == "merge" and self.current is not None:
                    by_id = {row["id"]: row for row in self.current["entries"]}
                    for row in incoming["entries"]:
                        if row["id"] not in by_id:
                            self.current["entries"].append(row)
                else:
                    self.current = incoming
                self.writes += 1
                self.revision = "rev:" + str(self.writes + 1)
                result = self.op(identifier, "write", "committed", snapshot=self.snapshot(self.current))
        elif op == "play":
            self.plays += 1
            result = {"operation_id": identifier, "accepted": True, "catalog_id": self.current["id"],
                      "entry_id": request["entry_id"], "file_index": request["file_index"]}
            self.op(identifier, "play", "accepted", target={key: result[key] for key in
                    ("catalog_id", "entry_id", "file_index")})
        else:
            raise AssertionError(op)
        response.update(result)
        return self.transform(request, response) if self.transform else response


class TorrentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = self.directory / "cli.json"
        self.clock = Clock()
        self.client = Client(self.clock, catalog())

    def run_cli(self, words, *, timeout=45, output=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        factory = cli.Session
        with mock.patch.object(cli, "Session", side_effect=lambda *a, **k:
                               factory(*a, **k, clock=self.clock, sleep=self.clock.sleep)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.run(ott, self.client, "device-1", words, timeout, output, self.path)
        return code, json.loads(stdout.getvalue()) if output else stdout.getvalue(), stderr.getvalue()

    def imported(self, value, name="input.json"):
        path = self.directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def operations(self):
        return [row[1]["op"] for row in self.client.calls if row[0] == "torrent_catalog"]

    def test_list_files_omit_source_and_preserve_native_indices(self):
        code, value, stderr = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(value["entries"][0]["id"], "entry_1")
        self.assertNotIn("private-token", json.dumps(value) + stderr)
        code, value, _ = self.run_cli(["files", "First"])
        self.assertEqual(code, 0)
        self.assertEqual([row["index"] for row in value["files"]], [3, 7])
        self.assertEqual(self.client.writes + self.client.plays, 0)

    def test_export_private_no_overwrite_and_import_roundtrip(self):
        path = self.directory / "export.json"
        self.assertEqual(self.run_cli(["export", str(path)])[0], 0)
        self.assertEqual(json.loads(path.read_text()), catalog())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        before = len(self.client.calls)
        self.assertEqual(self.run_cli(["export", str(path)])[0], 1)
        self.assertEqual(len(self.client.calls), before)
        self.client.current = None
        code, value, _ = self.run_cli(["import", str(path)])
        self.assertEqual((code, value["status"], self.client.current), (0, "committed", catalog()))
        self.assertEqual(self.client.writes, 1)
        self.assertEqual(self.run_cli(["import", str(path)])[1]["status"], "unchanged")
        self.assertEqual(self.client.writes, 1)

    def test_add_selected_files_persists_intents_before_requests(self):
        self.client.current = None
        def check(action, request):
            if "operation_id" in request and request["op"] in ("metadata", "write_begin"):
                files = list(self.directory.glob(".torrent-operations-*/*.json"))
                self.assertTrue(any(json.loads(path.read_text())["operation_id"] == request["operation_id"] for path in files))
                self.assertNotIn("private-token", "".join(path.read_text() for path in files))
        self.client.before = check
        code, value, stderr = self.run_cli(["add", SOURCE, "--name", "Season", "--files", "7"])
        self.assertEqual((code, value["status"]), (0, "committed"))
        entry = self.client.current["entries"][0]
        self.assertEqual((entry["source"], entry["title"], entry["category"]), (SOURCE, "Season", "Season"))
        self.assertEqual([row["index"] for row in entry["files"]], [7])
        self.assertEqual(self.operations().count("metadata"), 1)
        self.assertEqual(self.operations().count("write_commit"), 1)
        self.assertNotIn("private-token", stderr)

    def test_refresh_preserves_ids_labels_and_source(self):
        self.client.metadata_files = [{"index": 99, "name": "fresh.mkv", "length": 10}]
        self.assertEqual(self.run_cli(["refresh", "First"])[0], 0)
        expected = catalog()
        expected["entries"][0]["files"] = self.client.metadata_files
        self.assertEqual(self.client.current, expected)

    def test_rename_remove_only_selected_entry_no_play(self):
        self.client.current["entries"].append({**catalog()["entries"][0], "id": "other"})
        self.assertEqual(self.run_cli(["rename", "entry_1", "Renamed"])[0], 0)
        self.assertEqual(self.client.current["entries"][0]["category"], "Renamed")
        self.assertEqual(self.run_cli(["remove", "entry_1"])[0], 0)
        self.assertEqual([row["id"] for row in self.client.current["entries"]], ["other"])
        self.assertEqual(self.client.plays, 0)

    def test_ambiguous_title_has_ids_and_no_mutation(self):
        self.client.current["entries"].append({**catalog()["entries"][0], "id": "other"})
        code, value, _ = self.run_cli(["remove", "First"])
        self.assertEqual((code, value["candidate_ids"]), (1, ["entry_1", "other"]))
        self.assertEqual(self.client.writes, 0)

    def test_play_native_index_and_recoverable_status(self):
        self.assertEqual(self.run_cli(["play", "First", "0"])[0], 1)
        self.assertEqual(self.client.plays, 0)
        code, value, _ = self.run_cli(["play", "entry_1", "7"])
        self.assertEqual((code, value["status"], value["playback_confirmed"]), (0, "accepted", False))
        identifier = value["operation_id"]
        self.assertEqual(self.run_cli(["status", identifier])[1]["operation"]["target"]["file_index"], 7)
        self.assertEqual(self.client.plays, 1)

    def test_lost_play_or_commit_is_unknown_and_never_replayed(self):
        for failing in ("play", "write_commit"):
            with self.subTest(failing=failing):
                self.client = Client(self.clock, catalog())
                def transform(request, response):
                    if request["op"] == failing:
                        raise OSError("https://private.invalid/secret-token")
                    return response
                self.client.transform = transform
                words = ["play", "First", "3"] if failing == "play" else ["rename", "First", "New"]
                code, value, stderr = self.run_cli(words)
                self.assertEqual((code, value["status"]), (2, "unknown"))
                self.assertRegex(value["operation_id"], cli.UUID)
                self.assertEqual(self.operations().count(failing), 1)
                self.assertNotIn("secret-token", json.dumps(value) + stderr)

    def test_pending_metadata_and_cancel_do_not_add(self):
        self.client.pending = True
        code, value, _ = self.run_cli(["inspect", SOURCE], timeout=8)
        self.assertEqual((code, value["status"]), (2, "pending"))
        self.assertEqual(self.run_cli(["cancel", value["operation_id"]])[1]["status"], "cancelled")
        self.assertEqual(self.client.writes + self.client.plays, 0)

    def test_add_completed_metadata_does_not_refetch(self):
        code, value, _ = self.run_cli(["inspect", SOURCE])
        self.assertEqual(code, 0)
        self.client.calls.clear()
        self.assertEqual(self.run_cli(["add", "--metadata", value["operation_id"], "--name", "More"])[0], 0)
        self.assertNotIn("metadata", self.operations())
        self.assertEqual(len(self.client.current["entries"]), 2)

    def test_revision_drift_during_metadata_cannot_commit(self):
        self.client.drift = True
        self.client.metadata_files = [{"index": 1, "name": "changed.mkv", "length": 1}]
        code, value, _ = self.run_cli(["refresh", "First"])
        self.assertEqual((code, value["reason"]), (1, "revision_conflict"))
        self.assertEqual(self.client.writes, 0)
        self.assertEqual(self.operations().count("write_commit"), 1)

    def test_dry_run_and_merge_conflict_do_not_upload(self):
        incoming = catalog()
        incoming["entries"][0]["title"] = "Conflict"
        path = self.imported(incoming)
        code, value, _ = self.run_cli(["import", path, "replace", "--dry-run"])
        self.assertEqual((code, value["changed"]), (0, True))
        code, value, _ = self.run_cli(["import", path, "merge"])
        self.assertEqual((code, value["reason"]), (1, "entry_conflict"))
        self.assertFalse(any(op.startswith("write") for op in self.operations()))

    def test_runtime_hash_and_chunk_progress_corruption_rejected(self):
        def runtime(_, response):
            return {**response, "runtime": "wrong"}
        def digest(request, response):
            if request["op"] == "read":
                response["snapshot"]["sha256"] = "0" * 64
            return response
        def offset(request, response):
            if request["op"] == "read_chunk":
                response["offset"] += 1
            return response
        def eof(request, response):
            if request["op"] == "read_chunk":
                response["next_offset"] = 1
            return response
        for transform in (runtime, digest, offset, eof):
            self.client.transform = transform
            self.assertEqual(self.run_cli(["list"])[0], 1)
        self.assertEqual(self.client.writes + self.client.plays, 0)

    def test_whole_deadline_pacing_and_timeout_restoration(self):
        self.assertEqual(self.run_cli(["rename", "First", "New"])[0], 0)
        calls = self.client.calls
        self.assertTrue(all(b[2] - a[2] >= 1.5 for a, b in zip(calls, calls[1:])))
        self.assertTrue(all(row[2] + row[3] <= 145.00001 for row in calls))
        self.assertEqual(self.client.timeout, 45)
        self.client.calls.clear()
        self.assertEqual(self.run_cli(["rename", "New", "Last"], timeout=8)[0], 1)
        self.assertNotIn("write_begin", self.operations())

    def test_missing_malformed_capability_no_torrent_request(self):
        for marker in (None, {"version": True}, {"version": 1, "endpoint": SOURCE}):
            self.client.marker, self.client.calls = marker, []
            self.assertEqual(self.run_cli(["list"])[0], 1)
            self.assertEqual(self.operations(), [])

    def test_malformed_imports_fail_before_network(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"bad":NaN}', b'[[[[[[[[[0]]]]]]]]]', b'"\xff"'):
            path = self.directory / "bad.json"
            path.write_bytes(raw)
            self.assertEqual(self.run_cli(["import", str(path)])[0], 1)
        self.assertEqual(self.client.calls, [])

    def test_sparse_oversized_import_rejected_without_network(self):
        path = self.directory / "huge.json"
        with path.open("wb") as stream:
            stream.truncate(doc.MAX_BUNDLE_BYTES + 1)
        self.assertEqual(self.run_cli(["import", str(path)])[0], 1)
        self.assertEqual(self.client.calls, [])

    def test_label_privacy_in_text_and_json(self):
        self.client.current["entries"][0]["title"] = "\u200b/private/secret"
        self.client.current["entries"][0]["files"][0]["name"] = "https://private.invalid/secret.mkv"
        self.assertNotIn("/private/secret", self.run_cli(["list"])[1]["entries"][0]["title"])
        self.assertNotIn("private.invalid", self.run_cli(["files", "entry_1"], output=False)[1])

    def test_multi_chunk_roundtrip_hash_and_offsets(self):
        incoming = catalog()
        incoming["entries"][0]["files"] = [{"index": i * 3, "name": "episode-" + "x" * 200 + ".mkv",
                                           "length": i} for i in range(80)]
        code, value, _ = self.run_cli(["import", self.imported(incoming), "replace"], timeout=120)
        self.assertEqual((code, value["status"]), (0, "committed"))
        self.assertEqual(self.client.current, incoming)
        chunks = [row[1] for row in self.client.calls if row[1].get("op") == "write_chunk"]
        self.assertGreater(len(chunks), 2)
        self.assertEqual([row["offset"] for row in chunks], list(range(0, len(doc.canonical(incoming)), 6144)))
        self.assertTrue(all(len(base64.b64decode(row["data_b64"])) <= 6144 for row in chunks))

    def test_wrong_commit_readback_is_uncertain_not_success(self):
        def wrong(request, response):
            if request["op"] == "write_commit":
                response["operation"]["snapshot"] = self.client.snapshot(catalog())
            return response
        self.client.transform = wrong
        code, value, _ = self.run_cli(["rename", "First", "Changed"])
        self.assertEqual((code, value["status"], value["reason"]), (2, "unknown", "readback_mismatch"))
        self.assertEqual(self.operations().count("write_commit"), 1)

    def test_play_receipt_target_mismatch_is_uncertain(self):
        def wrong(request, response):
            if request["op"] == "play":
                response["catalog_id"] = "wrong"
            return response
        self.client.transform = wrong
        self.assertEqual(self.run_cli(["play", "First", "3"])[1]["status"], "unknown")
        self.assertEqual(self.client.plays, 1)

    def test_grammar_rejects_junk_before_network(self):
        for words in (["play", "First", "-1"], ["play", "First", "1e3"],
                      ["add", SOURCE, "--name", "x", "--files", "3,3"],
                      ["add", SOURCE], ["status", "not-uuid"], ["list", "extra"],
                      ["import", "a.json", "--dry-run", "--dry-run"], ["inspect", "file:///private/foo.torrent"]):
            with self.subTest(words=words):
                self.assertEqual(self.run_cli(words)[0], 1)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(cli.parse(["torrent", "play", "First", "003"])["index"], 3)

    def test_shared_wire_successes_and_negative_results(self):
        shared = json.loads((ROOT / "contracts/torrent-catalog-v1.json").read_bytes())
        for row in shared["valid"]:
            with self.subTest(name=row["name"]):
                if row["status"] == "ok":
                    cli.validate_response(row["data"], row["params"])
                else:
                    with self.assertRaises(cli.CatalogError) as raised:
                        cli.validate_response(row["data"], row["params"])
                    self.assertEqual(raised.exception.code, row["data"]["reason"])
        for row in shared["invalid_results"]:
            with self.subTest(name=row["name"]), self.assertRaises((cli.CatalogError, doc.DocumentError)):
                cli.validate_response(row["data"], row["params"])

    def test_document_bounds_and_playable_names(self):
        for mutate in (
            lambda c: c.update(endpoint="https://private.invalid"),
            lambda c: c.update(schema=True), lambda c: c.update(id="has:colon"),
            lambda c: c.update(name=" Leading"), lambda c: c.update(name="\ud800"),
            lambda c: c.update(name="\U0001f600" * 129),
            lambda c: c["entries"].append(copy.deepcopy(c["entries"][0])),
            lambda c: c["entries"][0]["files"].append(copy.deepcopy(c["entries"][0]["files"][0])),
            lambda c: c["entries"][0]["files"][0].update(length=True),
            lambda c: c["entries"][0]["files"][0].update(length=doc.MAX_SAFE + 1),
            lambda c: c["entries"][0]["files"][0].update(name="subtitle.srt"),
            lambda c: c["entries"][0].update(source="https://user:pass@example.invalid/a.torrent"),
        ):
            value = catalog()
            mutate(value)
            with self.assertRaises(doc.DocumentError):
                doc.catalog(value)
        value = catalog()
        value["name"] = "\U0001f600" * 128
        self.assertEqual(doc.catalog(value), value)
        for suffix in doc.PLAYABLE:
            self.assertEqual(doc.files([{"index": 0, "name": "a." + suffix.upper(), "length": 0}])[0]["index"], 0)
        with self.assertRaises(doc.DocumentError):
            doc.files([{"index": i, "name": "a.mkv", "length": 0} for i in range(4097)])

    def test_corrupt_storage_never_becomes_empty(self):
        self.client.current = {"unexpected": "private"}
        self.assertEqual(self.run_cli(["add", SOURCE, "--name", "x"])[0], 1)
        self.assertNotIn("metadata", self.operations())
        self.assertEqual(self.client.writes, 0)

    def test_large_deadline_and_invalid_clock(self):
        self.assertEqual(self.run_cli(["list"], timeout=10800)[0], 0)
        self.client.calls.clear()
        self.assertEqual(self.run_cli(["list"], timeout=10801)[0], 1)
        self.assertEqual(self.client.calls, [])
        self.client.before = lambda *_: setattr(self.clock, "value", self.clock.value - 5)
        self.assertEqual(self.run_cli(["list"])[1]["reason"], "context_changed")

    def bundle(self, channel_id="torrent:catalog_1:entry_1:3"):
        return {"version": 1, "kind": "ottplay-torrent-bundle", "catalog": catalog(),
                "favorites": {"version": 1, "entries": [{"title": "First", "origin": {
                    "provider": "torrent", "profile": "Torrent", "subprofile": None, "sourceAlias": "catalog_1"},
                    "locator": {"kind": "channel", "channelId": channel_id}}]}}

    def test_bundle_removed_references_preserved_without_mapping_authority(self):
        commands = ott.favorites_module("global_favorites_commands")
        value = self.bundle("torrent:catalog_1:removed:999")
        self.assertEqual(doc.bundle(value, commands.document), value)
        for channel in ("torrent:other:entry_1:3", "torrent:catalog_1:entry_1:03", "torrent:catalog_1:bad:id:3"):
            with self.assertRaises(doc.DocumentError):
                doc.bundle(self.bundle(channel), commands.document)

    def test_bundle_unsupported_preflight_before_catalog_write(self):
        self.client.current = None
        code, value, _ = self.run_cli(["import", self.imported(self.bundle()), "--with-favorites"])
        self.assertEqual((code, value["reason"]), (1, "favorites_unsupported"))
        self.assertNotIn("write_begin", self.operations())

    @contextlib.contextmanager
    def favorite_peer(self, *, fail=False, title_conflict=False):
        commands = ott.favorites_module("global_favorites_commands")
        self.client.extra_caps = {"favorites_global": {"version": 1}, "favorites_global_recovery": {"version": 1}}
        observations = []
        test = self
        class Peer:
            def __init__(self, client, device, **kwargs):
                observations.append(("new", test.client.writes, kwargs["deadline"]))

            def export(self):
                observations.append(("export", test.client.writes))
                value = test.bundle()["favorites"] if title_conflict else {"version": 1, "entries": []}
                if title_conflict:
                    value["entries"][0]["title"] = "Conflicting"
                return SimpleNamespace(document=value, description=SimpleNamespace(data={
                    "locator_kinds": ["channel"], "write_ready": True}))

            def apply(self, snapshot, incoming, mode):
                observations.append(("apply", test.client.writes, copy.deepcopy(incoming), mode))
                return SimpleNamespace(outcome="unknown" if fail else "verified", verified=not fail,
                                       operation_id="10000000-0000-4000-8000-000000000001")
        with mock.patch.object(commands.remote, "RemoteGlobalFavorites", Peer), \
                mock.patch.object(commands._Target, "require_write_ready", side_effect=lambda:
                                  observations.append(("recovery", test.client.writes))):
            yield observations

    def test_bundle_import_reuses_global_merge_and_fresh_admission_after_catalog(self):
        self.client.current = None
        with self.favorite_peer() as calls:
            code, value, _ = self.run_cli(["import", self.imported(self.bundle()), "--with-favorites"], timeout=120)
        self.assertEqual((code, value["status"], value["favorites"]["status"]), (0, "committed", "verified"))
        self.assertEqual([row[1] for row in calls if row[0] == "export"], [0, 1])
        self.assertEqual([row[1] for row in calls if row[0] == "recovery"], [0, 1])
        self.assertEqual([row[-1] for row in calls if row[0] == "apply"], ["merge"])

    def test_bundle_partial_retains_both_operation_ids(self):
        self.client.current = None
        with self.favorite_peer(fail=True):
            code, value, _ = self.run_cli(["import", self.imported(self.bundle()), "--with-favorites"], timeout=120)
        self.assertEqual((code, value["status"]), (2, "partial"))
        self.assertRegex(value["catalog"]["operation_id"], cli.UUID)
        self.assertRegex(value["favorites"]["operation_id"], cli.UUID)
        self.assertEqual(self.client.current, catalog())

    def test_bundle_conflict_and_dry_run_leave_both_stores_unchanged(self):
        self.client.current = None
        path = self.imported(self.bundle())
        with self.favorite_peer(title_conflict=True):
            code, value, _ = self.run_cli(["import", path, "--with-favorites"])
        self.assertEqual((code, value["reason"]), (1, "favorites_title_conflict"))
        with self.favorite_peer() as calls:
            code, value, _ = self.run_cli(["import", path, "--with-favorites", "--dry-run"])
        self.assertEqual((code, value["status"]), (0, "planned"))
        self.assertFalse(any(row[0] in ("apply", "recovery") for row in calls))
        self.assertNotIn("write_begin", self.operations())

    def test_bundle_export_filters_only_same_catalog_and_retains_missing_refs(self):
        commands = ott.favorites_module("global_favorites_commands")
        self.client.extra_caps = {"favorites_global": {"version": 1}}
        expected = self.bundle("torrent:catalog_1:removed:99")["favorites"]["entries"][0]
        wrong = copy.deepcopy(expected)
        wrong["locator"]["channelId"] = "torrent:other:entry_1:3"
        exported = SimpleNamespace(document={"version": 1, "entries": [expected, wrong]})
        path = self.directory / "bundle.json"
        with mock.patch.object(commands.remote.RemoteGlobalFavorites, "export", return_value=exported):
            code, value, _ = self.run_cli(["export", str(path), "--with-favorites"])
        self.assertEqual((code, value["status"]), (0, "exported"))
        result = json.loads(path.read_bytes())
        self.assertEqual(result["favorites"]["entries"], [expected])
        self.assertEqual(result["catalog"], catalog())
        self.assertEqual(self.client.writes, 0)

    def test_malicious_error_reason_and_extra_fields_never_leak(self):
        for payload in (
            {"error": "https://private.invalid/secret", "reason": "effect_failed"},
            {"error": "Torrent catalog request failed.", "reason": "secret://value"},
            {"error": "Torrent catalog request failed.", "reason": ["secret://value"]},
            {"error": "Torrent catalog request failed.", "reason": "effect_failed", "path": "/private/secret"},
        ):
            self.client.transform = lambda request, response: {
                "version": 1, "op": request["op"], "runtime": RUNTIME, **payload}
            code, value, stderr = self.run_cli(["list"])
            self.assertEqual(code, 1)
            self.assertNotIn("secret", json.dumps(value) + stderr)

    def test_export_bytes_exactly_match_canonical_document(self):
        path = self.directory / "canonical.json"
        self.assertEqual(self.run_cli(["export", str(path)])[0], 0)
        self.assertEqual(path.read_bytes(), doc.canonical(catalog()))

    def test_same_catalog_merge_preserves_order_name_and_idempotence(self):
        incoming = catalog()
        incoming["name"] = "Incoming name"
        incoming["entries"].append({**copy.deepcopy(incoming["entries"][0]), "id": "second", "title": "Second"})
        result = doc.merge(catalog(), incoming, "merge")
        self.assertEqual(result["name"], "Home")
        self.assertEqual([row["id"] for row in result["entries"]], ["entry_1", "second"])
        self.assertEqual(doc.merge(result, incoming, "merge"), result)
        self.assertEqual(doc.merge(catalog(), incoming, "replace"), incoming)


if __name__ == "__main__":
    unittest.main()
