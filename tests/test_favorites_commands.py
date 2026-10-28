"""Current Plex orchestration with private fixtures and an in-memory controller."""
import base64
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    import favorites_commands as commands
    import favorites_remote as remote
spec = importlib.util.spec_from_file_location("favorites_commands_memory", ROOT / "tests/test_favorites_remote.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
ott = fixture.ott
PACKET = json.loads((ROOT / "contracts/favorites-current-plex-v1.json").read_bytes())
SCOPE = copy.deepcopy(fixture.FIXTURE["scope"])
PMS = "synthetic-verified-pms-id"
BASE = json.loads(base64.b64decode(fixture.FIXTURE["fixtures"]["snapshot"]["response"]["document_base64"]))


class CurrentAPI(fixture.MemoryAPI):
    timeout = 45

    def __init__(self, clock, directory):
        super().__init__(clock)
        self.directory = directory
        self.documents = {"one": copy.deepcopy(BASE), "two": copy.deepcopy(BASE)}
        self.documents["two"]["scope"]["sourceBindingId"] = "00000000-0000-4000-8000-000000000099"
        self.runtime = fixture.RUNTIME
        self.capability = copy.deepcopy(PACKET["capability"]["favorites_transfer"])
        self.caps_change = lambda data: data

    def api(self, path, payload=None, timeout=None):
        self.device = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["device_id"][0]
        if payload is not None and payload["action"] == "capabilities":
            request_id = f"{len(self.responses) + 1:032x}"
            data = {"version": 1, "player": {"version": "1.1.70", "platform": "tauri", "runtime": self.runtime},
                    "lifecycle": [], "input": [], "playback": [], "favorites_transfer": copy.deepcopy(self.capability)}
            self.responses[request_id] = ("capabilities", {"id": request_id, "status": "ok", "data": self.caps_change(data)})
            self.calls.append({"method": "POST", "op": "capabilities", "at": self.clock(), "timeout": timeout})
            return 202, {"id": request_id, "status": "queued"}
        return super().api(path, payload, timeout)

    def data(self, params):
        op = params["op"]
        if op in ("bindingcatalog", "bind"):
            data = copy.deepcopy(PACKET[op + "_response"])
            if op == "bindingcatalog":
                data["binding_revision"] = "bindings-8"
                data["targets"][0].update(target_id="target-" + self.device, serverId=PMS)
                data["bindings"][0].update(scope=copy.deepcopy(self.documents[self.device]["scope"]),
                                           target_id="target-" + self.device)
            else:
                data.update(scope=copy.deepcopy(params["scope"]), target_id=params["target_id"],
                            binding_revision=params["expected_binding_revision"])
            data["runtime"] = params["runtime"]
            return data
        if op == "snapshot":
            encoded = remote.transfer.encode_document(commands._json(self.documents[self.device]))
            data = self.fixture(op)
            data.update(scope=copy.deepcopy(self.documents[self.device]["scope"]), runtime=params["runtime"],
                        document_base64=encoded.data_base64, document_sha256=encoded.sha256,
                        size_bytes=encoded.size_bytes)
            return data
        if op == "commit":
            # Real durable storage must precede the fake player's write.
            records = list(self.directory.glob(".favorites-operations-*/*.json"))
            assert records
            assert any(json.loads(path.read_bytes())["context"]["operation_id"] == params["operation_id"] for path in records)
            self.events.append("saved")
        data = super().data(params)
        if op == "preview":
            uploaded = json.loads(self.upload)
            memberships = [item for row in uploaded["channelLists"]["lists"] for item in row["entries"]]
            media = uploaded["mediaFavorites"]
            data["counts"] = {"channelLists": len(uploaded["channelLists"]["lists"]),
                              "channelMemberships": len(memberships), "mediaFavorites": len(media),
                              "unresolved": sum(row["state"] == "unresolved" for row in memberships + media)}
            self.preview = copy.deepcopy(data)
        if op == "commit":
            self.documents[self.device] = json.loads(self.upload)
        return data


class FavoritesCommandsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.path = self.directory / "cli.json"
        self.clock = fixture.Clock()
        self.client = CurrentAPI(self.clock, self.directory)
        saved = copy.deepcopy(BASE)
        saved["mediaFavorites"][0]["locator"]["ratingKey"] = "999"
        self.config = {"presets": {"Home": {}}, "token": "fixture-private-token",
                       "preset_favorites": {"version": 1, "presets": {"Home": commands._bundle(saved)}}}
        self.write_config()

    def write_config(self):
        self.path.write_bytes(commands._json(self.config))
        if os.name != "nt":
            self.path.chmod(0o600)

    def call(self, op, *, targets=None, timeout=45, **params):
        return commands.run(ott, self.client, self.config, self.path, targets or [("t1", "one")],
            {"op": op, **params}, timeout, monotonic=self.clock, sleep=self.clock.sleep,
            uuid_factory=lambda: fixture.OPERATION)

    def no_stage_or_context(self):
        self.assertNotIn("begin", self.client.posts())
        self.assertNotIn("commit", self.client.posts())
        self.assertEqual(list(self.directory.glob(".favorites-operations-*")), [])

    def test_bindings_and_diff_are_read_only_and_exact_current_source(self):
        result = self.call("bindings")
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["catalog"]["bindings"][0]["scope"], SCOPE)
        self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog"])
        result = self.call("diff", preset="Home")
        self.assertEqual(result["status"], "different")
        self.assertEqual(result["scope"], SCOPE)
        self.assertEqual(result["summary"]["added"], 1)
        self.no_stage_or_context()
        self.assertEqual(self.client.timeout, 45)

    def test_replace_saves_context_before_commit_and_marks_verified_completion(self):
        original = self.path.read_bytes()
        result = self.call("load", preset="Home", mode="replace")
        self.assertEqual((result["status"], result["verified"], result["warnings"]), ("verified", True, []))
        self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog", "snapshot", "begin", "append", "preview", "commit"])
        self.assertEqual(self.path.read_bytes(), original)
        records = list(self.directory.glob(".favorites-operations-*/*.json"))
        self.assertEqual(len(records), 1)
        self.assertIn("completion", json.loads(records[0].read_bytes()))
        if os.name != "nt":
            self.assertEqual(records[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(records[0].parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.client.documents["one"]["mediaFavorites"][0]["locator"]["ratingKey"], "999")
        self.assertLess(self.clock(), 145)

    def test_preview_load_and_clear_have_no_commit_callback_or_context_directory(self):
        for op, params in (("load", {"preset": "Home", "mode": "replace"}), ("clear", {})):
            with self.subTest(op=op):
                self.setUp()
                before = self.path.read_bytes()
                with mock.patch.object(commands._DurableContext, "save", side_effect=AssertionError("must not save")) as save:
                    result = self.call(op, dry_run=True, **params)
                self.assertEqual((result["status"], result["cleanup"], result["outcome"]), ("planned", "aborted", "not_applied"))
                save.assert_not_called()
                self.assertNotIn("commit", self.client.posts())
                self.assertEqual(list(self.directory.glob(".favorites-operations-*")), [])
                self.assertEqual(self.path.read_bytes(), before)

    def test_merge_conflict_stops_before_staging_and_clear_preserves_metadata(self):
        result = self.call("load", preset="Home", mode="merge")
        self.assertEqual(result["status"], "blocked")
        self.no_stage_or_context()
        before = copy.deepcopy(self.client.documents["one"])
        before["channelLists"]["lists"][0]["shuffleBefore"] = copy.deepcopy(before["mediaFavorites"])
        self.client.documents["one"] = before
        result = self.call("clear")
        self.assertTrue(result["verified"])
        after = self.client.documents["one"]
        self.assertEqual(after["channelLists"], before["channelLists"])
        self.assertEqual(after["mediaFavorites"], [])

    def test_invalid_capability_and_missing_saved_document_stop_without_mutation(self):
        for mutate in (lambda data: {key: value for key, value in data.items() if key != "favorites_transfer"},
                       lambda data: data | {"player": data["player"] | {"runtime": "bad runtime"}},
                       lambda data: data | {"favorites_transfer": data["favorites_transfer"] | {"version": 2}}):
            self.setUp()
            self.client.caps_change = mutate
            with self.assertRaises(ott.Error):
                self.call("clear")
            self.no_stage_or_context()
            self.assertEqual(self.client.posts(), ["capabilities"])
        self.setUp()
        self.config.pop("preset_favorites")
        self.write_config()
        with self.assertRaises(ott.Error):
            self.call("load", preset="Home", mode="replace")
        self.assertEqual(self.client.posts(), [])

    def test_pms_mismatch_in_media_channels_or_shuffle_is_not_name_matched(self):
        for collection in ("media", "channels", "shuffle"):
            self.setUp()
            saved = self.config["preset_favorites"]["presets"]["Home"]["scopes"][0]
            foreign = copy.deepcopy(saved["mediaFavorites"][0])
            foreign["locator"]["serverId"] = "foreign-server"
            if collection == "media":
                saved["mediaFavorites"] = [foreign]
            else:
                saved["channelLists"]["lists"][0]["entries" if collection == "channels" else "shuffleBefore"] = [foreign]
            self.write_config()
            with self.assertRaisesRegex(ott.Error, "plex source identity mismatch"):
                self.call("load", preset="Home", mode="replace")
            self.no_stage_or_context()
            self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog"])

    def test_ambiguous_unbound_and_unprovisioned_bind_never_mutate_registry(self):
        with self.assertRaisesRegex(ott.Error, "initial pairing not supported"):
            self.call("bind", preset="current", new=True)
        self.assertEqual(self.client.posts(), [])
        def ambiguous(data):
            second = copy.deepcopy(data["bindings"][0])
            second["scope"]["sourceBindingId"] = "00000000-0000-4000-8000-000000000077"
            return data | {"bindings": [*data["bindings"], second]}
        self.client.mutators["bindingcatalog"] = ambiguous
        with self.assertRaisesRegex(ott.Error, "scope not uniquely bound"):
            self.call("clear")
        self.no_stage_or_context()
        result = self.call("bind", preset="Home", scope=SCOPE, target_id="target-one")
        self.assertEqual(result["status"], "paired")
        self.assertEqual(self.client.posts().count("bind"), 1)
        self.client.mutators["bindingcatalog"] = lambda data: data | {"bindings": []}
        with self.assertRaises(ott.Error):
            self.call("bind", preset="Home")
        self.assertEqual(self.client.posts().count("bind"), 1)

    def test_collect_all_targets_once_then_one_local_cas_write(self):
        result = self.call("collect", preset="Home", mode="replace",
                           targets=[("t1", "one"), ("same", "one"), ("t2", "two")])
        self.assertEqual(result["players"], 2)
        self.assertEqual(len(result["sources"]), 2)
        self.assertTrue(all(row["coverage"] == "current-source" for row in result["sources"]))
        self.assertEqual(self.client.posts().count("snapshot"), 2)
        self.assertEqual(len(list(self.directory.glob("cli.json.favorites-backup-*.json"))), 1)
        saved = json.loads(self.path.read_bytes())["preset_favorites"]["presets"]["Home"]
        self.assertEqual(len(saved["scopes"]), 2)
        self.no_stage_or_context()

    def test_collect_incomplete_second_target_or_config_race_has_no_partial_write(self):
        original = self.path.read_bytes()
        self.client.documents["two"]["mediaFavorites"][0]["locator"]["serverId"] = "foreign-server"
        with self.assertRaises(ott.Error):
            self.call("collect", preset="Home", mode="replace", targets=[("t1", "one"), ("t2", "two")])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob("*favorites-backup*")), [])
        self.setUp()
        def changed(data):
            self.config["concurrent"] = True
            self.write_config()
            return data
        self.client.mutators["snapshot"] = changed
        with self.assertRaises(ott.Error):
            self.call("collect", preset="Home", mode="replace")
        self.assertTrue(json.loads(self.path.read_bytes())["concurrent"])
        self.assertEqual(list(self.directory.glob("*favorites-backup*")), [])

    def test_status_missing_is_unknown_and_verified_status_never_replays_commit(self):
        result = self.call("status", operation_id=fixture.OPERATION)
        self.assertEqual((result["status"], result["replay_allowed"]), ("unknown", False))
        self.assertEqual(self.client.posts(), [])
        self.assertEqual(list(self.directory.glob(".favorites-operations-*")), [])
        self.assertTrue(self.call("load", preset="Home", mode="replace")["verified"])
        posts = len(self.client.posts())
        self.client.runtime = "restarted-runtime"
        result = self.call("status", operation_id=fixture.OPERATION)
        self.assertTrue(result["verified"])
        self.assertEqual(result["warnings"], ["local_retention_unconfirmed"])
        self.assertEqual(self.client.posts()[posts:], ["capabilities", "bindingcatalog", "snapshot", "status"])
        self.assertEqual(self.client.posts().count("commit"), 1)

    def test_local_retention_failure_does_not_relabel_proven_player_outcome(self):
        with mock.patch.object(commands.operations.ContextStore, "mark_completed", side_effect=RuntimeError("private-token")):
            result = self.call("load", preset="Home", mode="replace")
        self.assertEqual((result["status"], result["verified"]), ("verified", True))
        self.assertEqual(result["warnings"], ["local_retention_unconfirmed"])
        self.assertNotIn("private-token", json.dumps(result))

    def test_same_runtime_status_reconciles_only_local_evidence_without_player_replay(self):
        # Simulate a confirmed player write whose local completion bookkeeping
        # failed: its durable begin/preview intent must remain available.
        with mock.patch.object(commands.operations.ContextStore, "mark_completed", side_effect=OSError("private-token")):
            applied = self.call("load", preset="Home", mode="replace")
        self.assertTrue(applied["verified"])
        records = list(self.directory.glob(".favorites-operations-*/*.json"))
        self.assertEqual(len(records), 1)
        record = records[0]
        pending_bytes = record.read_bytes()
        pending = json.loads(pending_bytes)
        self.assertNotIn("completion", pending)
        config_bytes = self.path.read_bytes()
        player_documents = copy.deepcopy(self.client.documents)
        files = set(self.directory.rglob("*"))

        for fail_retention in (True, False):
            posts = len(self.client.posts())
            if fail_retention:
                with mock.patch.object(commands.operations.ContextStore, "mark_completed", side_effect=OSError("private-token")):
                    result = self.call("status", operation_id=fixture.OPERATION)
                self.assertEqual(result["warnings"], ["local_retention_unconfirmed"])
                self.assertEqual(record.read_bytes(), pending_bytes)
            else:
                result = self.call("status", operation_id=fixture.OPERATION)
                self.assertEqual(result["warnings"], [])
                completed = json.loads(record.read_bytes())
                self.assertEqual(completed["context"], pending["context"])
                self.assertEqual(completed["completion"]["request"]["op"], "status")
                self.assertEqual(completed["completion"]["response"]["data"]["receipt"], self.client.committed["receipt"])
            self.assertEqual((result["status"], result["verified"], result["replay_allowed"]), ("verified", True, False))
            self.assertEqual(self.client.posts()[posts:], ["capabilities", "bindingcatalog", "status"])
            self.assertEqual(self.client.posts().count("commit"), 1)
            self.assertEqual(self.client.documents, player_documents)
            self.assertEqual(self.path.read_bytes(), config_bytes)
            self.assertEqual(set(self.directory.rglob("*")), files)
            self.assertNotIn("private-token", json.dumps(result))

    def test_retention_deadline_never_extends_or_replays_verified_player_write(self):
        def late(*_):
            self.clock.now = 146
            return {}
        with mock.patch.object(commands.operations.ContextStore, "mark_completed", side_effect=late):
            result = self.call("load", preset="Home", mode="replace")
        self.assertTrue(result["verified"])
        self.assertEqual(result["warnings"], ["local_retention_unconfirmed"])
        self.assertEqual(self.client.posts().count("commit"), 1)
        target = SimpleNamespace(deadline=100, now=lambda: 100, durable=mock.Mock())
        result = commands._completion(target, remote.Outcome(fixture.OPERATION, "verified", True, "verified_receipt"))
        self.assertTrue(result["verified"])
        target.durable.store.mark_completed.assert_not_called()

    def test_exact_json_value_node_and_root_depth_limits_and_count_caps(self):
        limits = copy.deepcopy(self.client.capability["limits"])
        target = {"serverId": PMS}
        limits.update(max_json_nodes=33, max_json_depth=5)
        self.assertEqual(commands._admit(BASE, target, limits), BASE)
        for changed in ({"max_json_nodes": 32}, {"max_json_depth": 4}, {"max_channel_lists": 1},
                        {"max_document_bytes": len(commands._json(BASE)) - 1}):
            with self.assertRaises(commands.CommandError):
                commands._admit(BASE, target, limits | changed)
        more = copy.deepcopy(BASE)
        more["mediaFavorites"] *= 2
        with self.assertRaises(commands.CommandError):
            commands._admit(more, target, self.client.capability["limits"] | {"max_media_favorites": 1})

    def test_whole_deadline_and_advertised_raw_metadata_limit_stop_before_staging(self):
        self.client.get_effects["capabilities"] = ["pending"] * 100
        with self.assertRaises(ott.Error):
            self.call("clear", timeout=8)
        self.assertLessEqual(self.clock(), 108)
        self.assertEqual(self.client.posts(), ["capabilities"])
        self.no_stage_or_context()
        self.setUp()
        self.client.capability["limits"]["max_metadata_bytes"] = 700
        self.client.raw_mutators["bindingcatalog"] = lambda raw: raw[:-2] + b" " * 701 + raw[-2:]
        with self.assertRaises(ott.Error):
            self.call("clear")
        self.no_stage_or_context()

    def test_unresolved_legacy_reference_is_preserved_and_blocked_preview_is_not_applied(self):
        saved = self.config["preset_favorites"]["presets"]["Home"]["scopes"][0]
        unresolved = {"state": "unresolved", "reason": "legacy-unportable", "provenance": {"origin": "raw", "legacyId": 123}}
        saved["mediaFavorites"] = [unresolved]
        self.write_config()
        self.client.mutators["preview"] = lambda data: data | {
            "ready": False, "unresolved": [{"collection": "mediaFavorites", "entry_index": 0, "reason": "legacy-unportable"}]}
        result = self.call("load", preset="Home", mode="replace", dry_run=True)
        self.assertEqual((result["status"], result["outcome"], result["cleanup"]), ("blocked", "not_applied", "aborted"))
        self.assertEqual(json.loads(self.client.upload)["mediaFavorites"], [unresolved])
        self.assertNotIn("commit", self.client.posts())
        self.assertEqual(list(self.directory.glob(".favorites-operations-*")), [])

    def test_lost_commit_and_existing_bind_ack_are_not_replayed(self):
        def lost(_api, _params):
            raise OSError("private-token")
        self.client.post_effects["bind"] = lost
        self.assertEqual(self.call("bind", preset="Home")["status"], "unknown")
        self.assertEqual(self.client.posts().count("bind"), 1)
        self.client.post_effects["commit"] = lost
        result = self.call("load", preset="Home", mode="replace")
        self.assertTrue(result["verified"])
        self.assertEqual(self.client.posts().count("commit"), 1)
        self.assertEqual(self.client.posts().count("status"), 1)

    def test_actual_client_serializer_raw_receipts_and_stable_authority(self):
        class Response:
            def __init__(inner, status, body):
                inner.status = status
                inner.stream = io.BytesIO(body if isinstance(body, bytes) else commands._json(body))
                inner.size = len(inner.stream.getvalue())
            def __enter__(inner):
                return inner
            def __exit__(inner, *_):
                return False
            @property
            def length(inner):
                return inner.size - inner.stream.tell()
            def read1(inner, size):
                return inner.stream.read(size)
        observed = []
        def open_request(request, timeout):
            observed.append(request)
            parsed = urllib.parse.urlsplit(request.full_url)
            payload = json.loads(request.data) if request.data is not None else None
            call = self.client.api if payload is not None else self.client.api_raw
            return Response(*call(parsed.path + "?" + parsed.query, payload, timeout))
        real = ott.Client.__new__(ott.Client)
        real.server, real.timeout, real.token = self.client.server, 45, "fixture-token"
        real.opener = mock.Mock(open=open_request)
        with mock.patch.object(ott.time, "monotonic", self.clock), mock.patch.object(ott, "read_json") as read:
            result = commands.run(ott, real, self.config, self.path, [("t1", "one")], {"op": "clear", "dry_run": True},
                45, monotonic=self.clock, sleep=self.clock.sleep, uuid_factory=lambda: fixture.OPERATION)
        self.assertEqual(result["status"], "planned")
        read.assert_not_called()
        self.assertEqual(real.timeout, 45)
        self.assertEqual(sum(request.get_method() == "POST" for request in observed), 7)
        for request in observed:
            if request.data is not None:
                self.assertEqual(request.data, json.dumps(json.loads(request.data), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(list(self.directory.glob(".favorites-operations-*")), [])


if __name__ == "__main__":
    unittest.main()
