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
CANONICAL_PACKET = json.loads((ROOT / "contracts/favorites-current-plex-protocol-v1.json").read_bytes())
SCOPE = copy.deepcopy(fixture.FIXTURE["scope"])
PRIOR_SCOPE = {"provider": "plex", "profileBindingId": "00000000-0000-4000-8000-000000000077",
               "sourceBindingId": "00000000-0000-4000-8000-000000000078"}
PMS = "synthetic-verified-pms-id"
BASE = json.loads(base64.b64decode(fixture.FIXTURE["fixtures"]["snapshot"]["response"]["document_base64"]))


class CurrentAPI(fixture.MemoryAPI):
    timeout = 45

    def __init__(self, clock, directory):
        super().__init__(clock)
        self.directory = directory
        self.documents = {"one": copy.deepcopy(BASE), "two": copy.deepcopy(BASE)}
        self.documents["two"]["scope"]["sourceBindingId"] = "00000000-0000-4000-8000-000000000099"
        self.bindings = {device: [copy.deepcopy(value["scope"])] for device, value in self.documents.items()}
        self.binding_revisions = {device: 8 for device in self.documents}
        # Synthetic player-owned UUIDs; these are deliberately unrelated to the
        # host's transfer-operation UUID factory or a preset's stored identity.
        self.created_scope = {"provider": "plex",
                              "profileBindingId": "00000000-0000-4000-8000-000000000071",
                              "sourceBindingId": "00000000-0000-4000-8000-000000000072"}
        self.runtime = fixture.RUNTIME
        self.capability = copy.deepcopy(CANONICAL_PACKET["capability"]["favorites_transfer"])
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
                data["binding_revision"] = str(self.binding_revisions[self.device])
                data["targets"][0].update(target_id="target-" + self.device, serverId=PMS)
                data["bindings"] = [{"scope": copy.deepcopy(scope), "target_id": "target-" + self.device,
                                     "codecVersion": 1} for scope in self.bindings[self.device]]
            else:
                data.update(scope=copy.deepcopy(params["scope"]), target_id=params["target_id"],
                            binding_revision=params["expected_binding_revision"])
            data["runtime"] = params["runtime"]
            return data
        if op == "provision":
            assert params["target_id"] == "target-" + self.device
            assert params["expected_binding_revision"] == str(self.binding_revisions[self.device])
            current = self.bindings[self.device]
            if params["mode"] == "create":
                assert not current and "scope" not in params and "prior_scope" not in params
                selected = copy.deepcopy(self.created_scope)
            else:
                selected = copy.deepcopy(params["scope"])
                if params["mode"] == "replace-current":
                    assert current == [params["prior_scope"]] and params["prior_scope"] != selected
                else:
                    assert params["mode"] in ("import", "pair") and not current and "prior_scope" not in params
            self.bindings[self.device] = [selected]
            self.binding_revisions[self.device] += 1
            self.documents[self.device]["scope"] = copy.deepcopy(selected)
            return {"version": 1, "op": op, "runtime": params["runtime"], "mode": params["mode"],
                    "paired": True, "changed": True, "binding_revision": str(self.binding_revisions[self.device]),
                    "target_id": params["target_id"], "scope": copy.deepcopy(selected),
                    "persistence": "persistent", "outcome": "verified"}
        if op == "snapshot":
            encoded = remote.transfer.encode_document(commands._json(self.documents[self.device]))
            data = self.fixture(op)
            data.update(scope=copy.deepcopy(self.documents[self.device]["scope"]), runtime=params["runtime"],
                        document_base64=encoded.data_base64, document_sha256=encoded.sha256,
                        size_bytes=encoded.size_bytes, binding_revision=str(self.binding_revisions[self.device]))
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

    def call(self, op, *, targets=None, timeout=45, uuid_factory=None, **params):
        return commands.run(ott, self.client, self.config, self.path, targets or [("t1", "one")],
            {"op": op, **params}, timeout, monotonic=self.clock, sleep=self.clock.sleep,
            uuid_factory=uuid_factory or (lambda: fixture.OPERATION))

    def provision_requests(self):
        return [json.loads(row["payload"])["params"] for row in self.client.calls
                if row["method"] == "POST" and row["op"] == "provision"]

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
        self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog", "snapshot", "prepare", "begin", "append", "preview", "commit"])
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

    def test_create_uses_player_generated_pair_once_without_local_identity_or_context(self):
        self.client.bindings["one"] = []
        original = self.path.read_bytes()
        uuid_factory = mock.Mock(side_effect=AssertionError("host must not generate pair UUIDs"))
        with mock.patch.object(commands._DurableContext, "save", side_effect=AssertionError("not a transfer")):
            result = self.call("bind", new=True, uuid_factory=uuid_factory)
        uuid_factory.assert_not_called()
        self.assertEqual((result["status"], result["scope"], result["replay_allowed"]),
                         ("paired", self.client.created_scope, False))
        self.assertEqual(result["pairing"]["outcome"], "verified")
        self.assertNotIn("verified", result)  # Binding proof cannot authorize a transfer receipt.
        self.assertEqual(self.provision_requests(), [{"op": "provision", "runtime": fixture.RUNTIME,
            "mode": "create", "target_id": "target-one", "expected_binding_revision": "8", "codecVersion": 1}])
        self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog", "provision"])
        self.assertEqual(self.path.read_bytes(), original)
        self.no_stage_or_context()

    def test_existing_exact_new_or_saved_binding_never_sends_mutating_rpc(self):
        for params in ({"new": True}, {"preset": "Home"}, {"preset": "Home", "scope": SCOPE}):
            with self.subTest(params=params):
                self.setUp()
                before = copy.deepcopy(self.client.bindings)
                result = self.call("bind", **params)
                self.assertEqual((result["status"], result["scope"]), ("unchanged", SCOPE))
                self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog"])
                self.assertEqual(self.client.bindings, before)
                self.no_stage_or_context()

    def test_explicit_existing_scope_disambiguates_but_replacement_requires_unique_prior(self):
        second = SCOPE | {"sourceBindingId": "00000000-0000-4000-8000-000000000077"}
        self.client.bindings["one"].append(second)
        for op, params in (("clear", {}), ("bind", {"new": True}), ("bind", {"preset": "Home"})):
            with self.subTest(op=op, params=params), self.assertRaisesRegex(ott.Error, "scope not uniquely bound"):
                self.call(op, **params)
        result = self.call("bind", preset="Home", scope=SCOPE, target_id="target-one")
        self.assertEqual((result["status"], result["scope"]), ("unchanged", SCOPE))
        third = SCOPE | {"sourceBindingId": "00000000-0000-4000-8000-000000000078"}
        self.config["preset_favorites"]["presets"]["Home"]["scopes"][0]["scope"] = third
        self.write_config()
        with self.assertRaisesRegex(ott.Error, "scope not uniquely bound"):
            self.call("bind", preset="Home", scope=third, replace_current=True)
        self.assertNotIn("bind", self.client.posts())
        self.assertEqual(self.provision_requests(), [])
        self.no_stage_or_context()

    def test_unbound_saved_pair_imports_exact_scope_and_revision_for_selected_device(self):
        self.client.bindings["two"] = []
        self.client.binding_revisions["two"] = 23
        unchanged_one = copy.deepcopy(self.client.bindings["one"])
        result = self.call("bind", preset="Home", targets=[("t2", "two")], scope=SCOPE, target_id="target-two")
        self.assertEqual((result["status"], result["scope"]), ("paired", SCOPE))
        self.assertEqual(self.provision_requests(), [{"op": "provision", "runtime": fixture.RUNTIME,
            "mode": "import", "target_id": "target-two", "expected_binding_revision": "23",
            "codecVersion": 1, "scope": SCOPE}])
        self.assertEqual(self.client.bindings["one"], unchanged_one)
        self.assertEqual(self.client.bindings["two"], [SCOPE])
        self.no_stage_or_context()

    def test_different_pair_requires_explicit_replacement_with_exact_prior(self):
        prior = copy.deepcopy(PRIOR_SCOPE)
        self.client.bindings["one"] = [prior]
        with self.assertRaisesRegex(ott.Error, "different pair requires replace current"):
            self.call("bind", preset="Home", scope=SCOPE)
        self.assertEqual(self.provision_requests(), [])
        result = self.call("bind", preset="Home", scope=SCOPE, replace_current=True)
        self.assertEqual(result["status"], "paired")
        self.assertEqual(self.provision_requests(), [{"op": "provision", "runtime": fixture.RUNTIME,
            "mode": "replace-current", "target_id": "target-one", "expected_binding_revision": "8",
            "codecVersion": 1, "scope": SCOPE, "prior_scope": prior}])
        self.no_stage_or_context()

    def test_pairing_checks_explicit_target_and_saved_scope_before_provision(self):
        self.client.bindings["one"] = []
        absent = SCOPE | {"sourceBindingId": "00000000-0000-4000-8000-000000000077"}
        for params, reason in (({"preset": "Home", "target_id": "target-two"}, "target not current"),
                               ({"new": True, "target_id": "target-two"}, "target not current"),
                               ({"preset": "Home", "scope": absent}, "saved scope not uniquely bound")):
            with self.subTest(params=params), self.assertRaisesRegex(ott.Error, reason):
                self.call("bind", **params)
        self.assertEqual(self.provision_requests(), [])
        self.no_stage_or_context()

    def test_unbound_pairing_rejects_foreign_pms_in_all_located_collections(self):
        for collection in ("media", "channels", "shuffle"):
            with self.subTest(collection=collection):
                self.setUp()
                self.client.bindings["one"] = []
                saved = self.config["preset_favorites"]["presets"]["Home"]["scopes"][0]
                foreign = copy.deepcopy(saved["mediaFavorites"][0])
                foreign["locator"]["serverId"] = "foreign-server"
                if collection == "media":
                    saved["mediaFavorites"] = [foreign]
                else:
                    saved["channelLists"]["lists"][0]["entries" if collection == "channels" else "shuffleBefore"] = [foreign]
                self.write_config()
                with self.assertRaisesRegex(ott.Error, "plex source identity mismatch"):
                    self.call("bind", preset="Home", scope=SCOPE)
                self.assertEqual(self.provision_requests(), [])
                self.no_stage_or_context()

    def test_channel_or_shuffle_only_document_can_establish_exact_current_pms(self):
        for collection in ("entries", "shuffleBefore"):
            with self.subTest(collection=collection):
                self.setUp()
                self.client.bindings["one"] = []
                saved = self.config["preset_favorites"]["presets"]["Home"]["scopes"][0]
                located = copy.deepcopy(saved["mediaFavorites"][0])
                saved["mediaFavorites"] = []
                for row in saved["channelLists"]["lists"]:
                    row["entries"] = []
                    row.pop("shuffleBefore", None)
                saved["channelLists"]["lists"][0][collection] = [located]
                self.write_config()
                result = self.call("bind", preset="Home")
                self.assertEqual((result["status"], result["scope"]), ("paired", SCOPE))
                self.assertEqual(self.provision_requests()[0]["mode"], "import")
                self.no_stage_or_context()

    def test_empty_or_unresolved_unbound_document_cannot_establish_pms(self):
        for unresolved in (False, True):
            with self.subTest(unresolved=unresolved):
                self.setUp()
                self.client.bindings["one"] = []
                saved = self.config["preset_favorites"]["presets"]["Home"]["scopes"][0]
                saved["mediaFavorites"] = []
                for row in saved["channelLists"]["lists"]:
                    row["entries"] = []
                    row.pop("shuffleBefore", None)
                if unresolved:
                    saved["mediaFavorites"] = [{"state": "unresolved", "reason": "legacy-unportable",
                                                 "provenance": {"origin": "raw", "legacyId": 123}}]
                self.write_config()
                with self.assertRaisesRegex(ott.Error, "saved source identity unavailable"):
                    self.call("bind", preset="Home", scope=SCOPE)
                self.assertEqual(self.provision_requests(), [])
                self.no_stage_or_context()

    def test_pairing_dry_runs_do_not_mutate_or_generate_ids_or_save_context(self):
        for mode in ("create", "import", "replace-current"):
            with self.subTest(mode=mode):
                self.setUp()
                self.client.bindings["one"] = [] if mode != "replace-current" else [copy.deepcopy(PRIOR_SCOPE)]
                before = copy.deepcopy(self.client.bindings)
                original = self.path.read_bytes()
                params = {"new": True} if mode == "create" else {"preset": "Home", "scope": SCOPE}
                if mode == "replace-current":
                    params["replace_current"] = True
                uuid_factory = mock.Mock(side_effect=AssertionError("must not generate identities"))
                with mock.patch.object(commands._DurableContext, "save", side_effect=AssertionError("must not save")):
                    result = self.call("bind", dry_run=True, uuid_factory=uuid_factory, **params)
                self.assertEqual((result["status"], result["mode"]), ("planned", mode))
                self.assertEqual(result["scope"], None if mode == "create" else SCOPE)
                uuid_factory.assert_not_called()
                self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog"])
                self.assertEqual(self.client.bindings, before)
                self.assertEqual(self.path.read_bytes(), original)
                self.no_stage_or_context()

    def test_invalid_provision_metadata_is_refused_before_dry_run_or_mutation(self):
        for case in ("revision", "target", "overlapping_pair"):
            for dry_run in (False, True):
                with self.subTest(case=case, dry_run=dry_run):
                    self.setUp()
                    self.client.bindings["one"] = []
                    params = {"new": True}
                    if case == "revision":
                        self.client.mutators["bindingcatalog"] = lambda data: data | {"binding_revision": "08"}
                    elif case == "target":
                        def non_ascii_target(data):
                            data["targets"][0]["target_id"] = "é"
                            return data
                        self.client.mutators["bindingcatalog"] = non_ascii_target
                    else:
                        self.client.bindings["one"] = [SCOPE | {"sourceBindingId": PRIOR_SCOPE["sourceBindingId"]}]
                        params = {"preset": "Home", "scope": SCOPE, "replace_current": True}
                    with self.assertRaises(ott.Error):
                        self.call("bind", dry_run=dry_run, **params)
                    self.assertEqual(self.provision_requests(), [])
                    self.no_stage_or_context()

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

    def test_lost_pairing_ack_is_not_replayed_and_fresh_observation_remains_unknown(self):
        def lost(_api, _params):
            raise OSError("private-token")
        for mode in ("create", "import", "replace-current"):
            with self.subTest(mode=mode):
                self.setUp()
                self.client.bindings["one"] = [] if mode != "replace-current" else [copy.deepcopy(PRIOR_SCOPE)]
                params = {"new": True} if mode == "create" else {"preset": "Home", "scope": SCOPE}
                if mode == "replace-current":
                    params["replace_current"] = True
                self.client.post_effects["provision"] = lost
                result = self.call("bind", **params)
                self.assertEqual((result["status"], result["request_id"], result["replay_allowed"]), ("unknown", None, False))
                self.assertEqual(result["observed_pair"]["scope"], self.client.created_scope if mode == "create" else SCOPE)
                self.assertEqual(result["observed_pair"]["binding_revision"], "9")
                self.assertNotIn("verified", result)
                self.assertNotIn("private-token", json.dumps(result))
                self.assertEqual(self.client.posts(), ["capabilities", "bindingcatalog", "provision", "bindingcatalog"])
                self.assertEqual(len(self.provision_requests()), 1)
                self.no_stage_or_context()

    def test_malformed_pairing_ack_preserves_known_request_id_without_replay(self):
        self.client.bindings["one"] = []
        # The actual import was applied, but its ACK echoes a different target.
        self.client.mutators["provision"] = lambda data: data | {"target_id": "target-two"}
        result = self.call("bind", preset="Home", scope=SCOPE)
        self.assertEqual((result["status"], result["request_id"]), ("unknown", "00000000000000000000000000000003"))
        self.assertEqual(result["observed_pair"]["scope"], SCOPE)
        self.assertEqual(len(self.provision_requests()), 1)
        self.no_stage_or_context()

    def test_changed_target_observation_never_upgrades_uncertain_pairing(self):
        self.client.bindings["one"] = []
        def lost(api, _params):
            def changed(data):
                data["targets"][0]["serverId"] = "different-server"
                return data
            api.mutators["bindingcatalog"] = changed
            raise OSError("private-token")
        self.client.post_effects["provision"] = lost
        result = self.call("bind", new=True)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("observed_pair", result)
        self.assertEqual(len(self.provision_requests()), 1)
        self.no_stage_or_context()

    def test_pending_pairing_reserves_observation_time_and_never_extends_whole_deadline(self):
        self.client.bindings["one"] = []
        self.client.get_effects["provision"] = ["pending"] * 100
        result = self.call("bind", new=True, timeout=12)
        self.assertEqual((result["status"], result["replay_allowed"]), ("unknown", False))
        self.assertEqual(len(self.provision_requests()), 1)
        self.assertEqual(self.client.posts().count("bindingcatalog"), 2)
        self.assertLessEqual(self.clock(), 112)
        for row in self.client.calls:
            deadline = 108 if row["op"] == "provision" else 112
            self.assertGreater(row["timeout"], 0)
            self.assertLessEqual(row["at"] + row["timeout"], deadline)
        self.no_stage_or_context()

    def test_lost_commit_ack_is_not_replayed(self):
        def lost(_api, _params):
            raise OSError("private-token")
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
        self.assertEqual(sum(request.get_method() == "POST" for request in observed), 8)
        for request in observed:
            if request.data is not None:
                self.assertEqual(request.data, json.dumps(json.loads(request.data), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(list(self.directory.glob(".favorites-operations-*")), [])


if __name__ == "__main__":
    unittest.main()
