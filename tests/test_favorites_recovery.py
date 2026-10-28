"""Frozen recovery v2 wire, durable administrative intent and actual host routing."""
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
sys.path.insert(0, str(ROOT / "cli"))
import favorites_commands as commands
import favorites_operations as operations
import favorites_protocol as protocol
import favorites_remote as remote

spec = importlib.util.spec_from_file_location("recovery_host_fixture", ROOT / "tests/test_favorites_commands.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
ott = host.ott
FIX = json.loads((ROOT / "docs/design/favorites-recovery-v1.fixtures.json").read_bytes())
EMPTY = json.loads((ROOT / "docs/design/favorites-recovery-v1-empty.fixtures.json").read_bytes())
RUNTIME = FIX["inspect"]["request"]["runtime"]
OPERATION = FIX["inspect"]["request"]["operation_id"]
PLAN = FIX["inspect"]["result"]["plan_sha256"]


def checked(name="inspect"):
    pair = FIX[name]
    return protocol.validate_result(pair["request"], pair["result"], request_id="a" * 32,
                                    response_id="a" * 32, status="ok")


class RecoveryProtocolTests(unittest.TestCase):
    def test_frozen_fixtures_and_original_digests(self):
        for name in ("list", "inspect", "acknowledge", "inspect_after_lost_ack", "legacy_inspect"):
            self.assertEqual(checked(name).data, FIX[name]["result"])
            self.assertFalse(checked(name).verified)
        self.assertEqual(protocol.recovery_ack_request(checked()), FIX["acknowledge"]["request"])
        for key in ("original_entry", "before_ledger", "after_ledger"):
            self.assertEqual(hashlib.sha256(FIX["derivation"][key + "_utf8"].encode()).hexdigest(),
                             FIX["derivation"][key + "_sha256"])
        for name in ("list", "inspect"):
            pair = EMPTY[name]
            protocol.validate_result(pair["request"], pair["result"], request_id="a" * 32,
                                     response_id="a" * 32, status="ok")

    def test_strict_shapes_hash_bindings_original_outcome_and_global_provider(self):
        for key in ("acknowledgement_id", "after_ledger_sha256", "plan_sha256", "backend_proof", "original_entry_sha256"):
            with self.subTest(key=key):
                request = copy.deepcopy(FIX["acknowledge"]["request"])
                request[key] = "f" * 64
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_request(request)
        for field, value in (("accept_unknown", 1), ("reason", "private text"), ("extra", True)):
            request = copy.deepcopy(FIX["acknowledge"]["request"])
            request[field] = value
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_request(request)
        pair = copy.deepcopy(FIX["legacy_inspect"])
        pair["result"]["record"]["scope"]["provider"] = "retired/vendor-2"
        protocol.validate_result(pair["request"], pair["result"], request_id="a" * 32, response_id="a" * 32, status="ok")
        pair["result"]["record"]["original_outcome"] = "partial"
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_result(pair["request"], pair["result"], request_id="a" * 32, response_id="a" * 32, status="ok")

    def test_raw_metadata_limit_duplicate_keys_sorted_unique_rows_and_errors(self):
        pair = FIX["list"]
        response = {"id": "a" * 32, "status": "ok", "data": pair["result"]}
        raw = json.dumps(response).encode()
        for bad in (raw.replace(b'"version": 1', b'"version": 1,"version":1', 1),
                    raw.replace(b'"records": [', b'"records": [' + b' ' * 131072, 1)):
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_response(pair["request"], bad, request_id="a" * 32)
        data = copy.deepcopy(pair["result"])
        data["records"] *= 2
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_result(pair["request"], data, request_id="a" * 32, response_id="a" * 32, status="ok")
        for error in protocol.RECOVERY_ERRORS:
            protocol.validate_result(pair["request"], {"version": 1, "op": "recovery", "runtime": RUNTIME, "error": error},
                                     request_id="a" * 32, response_id="a" * 32, status="rejected")
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_result(pair["request"], {"version": 1, "op": "recovery", "runtime": RUNTIME, "error": "codec_error"},
                                     request_id="a" * 32, response_id="a" * 32, status="rejected")

    def test_inspect_plan_requires_all_three_digests_and_stored_audit_can_cross_runtime(self):
        for key in ("plan_sha256", "acknowledgement_id", "after_ledger_sha256"):
            data = copy.deepcopy(FIX["inspect"]["result"])
            data[key] = None
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_result(FIX["inspect"]["request"], data, request_id="a" * 32, response_id="a" * 32, status="ok")
        pair = copy.deepcopy(FIX["inspect_after_lost_ack"])
        pair["request"]["runtime"] = pair["result"]["runtime"] = "restarted-page"
        result = protocol.validate_result(pair["request"], pair["result"], request_id="a" * 32, response_id="a" * 32, status="ok")
        self.assertEqual(result.data["record"]["acknowledgement"]["runtime"], RUNTIME)
        self.assertFalse(result.verified)
        pair["result"]["record"]["acknowledgement"]["runtime"] = "restarted-page"
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_result(pair["request"], pair["result"], request_id="a" * 32, response_id="a" * 32, status="ok")

    def test_recomputed_commitments_cannot_ack_absent_or_unchanged_ledgers(self):
        original = FIX["acknowledge"]["request"]
        def digest(values):
            return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        for before, after in ((original["expected_ledger_revision"], protocol.EMPTY_LEDGER_SHA256),
                              (original["expected_ledger_revision"], original["expected_ledger_revision"]),
                              (protocol.EMPTY_LEDGER_SHA256, original["after_ledger_sha256"])):
            with self.subTest(before=before, after=after):
                request = copy.deepcopy(original)
                request.update(expected_ledger_revision=before, after_ledger_sha256=after)
                scope = request["scope"]
                values = [RUNTIME, OPERATION, [scope["profileBindingId"], scope["sourceBindingId"], scope["provider"]],
                          request["original_entry_sha256"], before, request["backend_proof"], protocol.RECOVERY_REASON]
                request["acknowledgement_id"] = digest(["ottplay-favorites-recovery-ack-v1", *values])
                request["plan_sha256"] = digest(["ottplay-favorites-recovery-plan-v1", *values,
                                                request["acknowledgement_id"], after])
                with self.assertRaises(protocol.ProtocolError):
                    protocol.recovery_digests(RUNTIME, OPERATION, scope, request["original_entry_sha256"],
                                              before, request["backend_proof"], after)
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_request(request)
                inspected = copy.deepcopy(FIX["inspect"]["result"])
                inspected.update(ledger_revision=before, after_ledger_sha256=after,
                                 acknowledgement_id=request["acknowledgement_id"], plan_sha256=request["plan_sha256"])
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_result(FIX["inspect"]["request"], inspected,
                                             request_id="a" * 32, response_id="a" * 32, status="ok")
                acknowledged = copy.deepcopy(FIX["acknowledge"]["result"])
                acknowledged.update(ledger_revision=after, acknowledgement_id=request["acknowledgement_id"],
                                    plan_sha256=request["plan_sha256"])
                acknowledged["record"]["acknowledgement"] = protocol.recovery_audit(request)
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_result(request, acknowledged, request_id="a" * 32, response_id="a" * 32, status="ok")

    def test_historical_audit_cannot_claim_an_absent_before_ledger(self):
        row = copy.deepcopy(FIX["inspect_after_lost_ack"]["result"]["record"])
        audit, scope = row["acknowledgement"], row["scope"]
        audit["ledger_revision_before"] = protocol.EMPTY_LEDGER_SHA256
        values = ["ottplay-favorites-recovery-ack-v1", audit["runtime"], row["operation_id"],
                  [scope["profileBindingId"], scope["sourceBindingId"], scope["provider"]],
                  row["original_entry_sha256"], audit["ledger_revision_before"], audit["backend_proof"], audit["reason"]]
        audit["acknowledgement_id"] = hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()
        for name in ("list", "inspect_after_lost_ack"):
            with self.subTest(name=name):
                pair = copy.deepcopy(FIX[name])
                if name == "list":
                    pair["result"]["records"] = [row]
                else:
                    pair["result"]["record"] = row
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_result(pair["request"], pair["result"],
                                             request_id="a" * 32, response_id="a" * 32, status="ok")

    def test_ack_correlates_after_image_and_observed_record_and_marker_strictness(self):
        for value in ({"version": True}, {"version": 2}, {"version": 1, "extra": 0}, None):
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_recovery_capability(value)
        for key in ("ledger_revision", "plan_sha256", "acknowledgement_id", "operation_id"):
            data = copy.deepcopy(FIX["acknowledge"]["result"])
            data[key] = "f" * 64
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_result(FIX["acknowledge"]["request"], data, request_id="a" * 32, response_id="a" * 32, status="ok")


class RecoveryAPI(host.CurrentAPI):
    def __init__(self, clock, directory):
        super().__init__(clock, directory)
        self.runtime = RUNTIME
        self.acked = False
        self.lose_ack = False
        self.invalid_ack = False
        self.rows = True
        self.inspect_mode = None
        self.effects = 0
        self.older_ack_race = False

    def data(self, params):
        if params["op"] != "recovery":
            return super().data(params)
        mode = params["mode"]
        if mode == "acknowledge":
            assert params == FIX["acknowledge"]["request"]
            if self.acked:
                return {"version": 1, "runtime": RUNTIME, "op": "recovery", "error": "revision_conflict"}
            self.acked = True
            self.effects += 1
            value = copy.deepcopy(FIX[mode]["result"])
            if self.invalid_ack:
                value["record"]["original_outcome"] = "partial"
            return value
        if not self.rows:
            return copy.deepcopy(EMPTY[mode]["result"])
        name = "inspect_after_lost_ack" if self.acked and mode == "inspect" else mode
        if mode == "inspect" and self.inspect_mode:
            name = self.inspect_mode
        return copy.deepcopy(FIX[name]["result"])

    def api(self, path, payload=None, timeout=None):
        acknowledge = payload and payload.get("params", {}).get("mode") == "acknowledge"
        if acknowledge and self.older_ack_race:
            # The older queued attempt wins the physical lock after the fresh
            # inspect. Exact ledger CAS rejects the new conditional request.
            self.older_ack_race = False
            self.acked = True
            self.effects += 1
        value = super().api(path, payload, timeout)
        if payload and payload.get("action") == "favorites_transfer":
            response = self.responses[value[1]["id"]][1]
            if "error" in response["data"]:
                response["status"] = "rejected"
        if acknowledge and self.lose_ack:
            raise OSError("private endpoint must never be displayed")
        return value


class RecoveryHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.path = self.directory / "cli.json"
        self.config = {"presets": {"Home": {}}}
        self.path.write_text(json.dumps(self.config))
        if os.name != "nt":
            self.path.chmod(0o600)
        self.clock = host.fixture.Clock()
        self.client = RecoveryAPI(self.clock, self.directory)

    def call(self, words, *, targets=None, timeout=45):
        params = ott.parse_command(["fav", *words])[1]
        return commands.run(ott, self.client, self.config, self.path, targets or [("t1", "one")], params, timeout,
                            monotonic=self.clock, sleep=self.clock.sleep)

    def modes(self):
        return [json.loads(row["payload"])["params"]["mode"] for row in self.client.calls
                if row["method"] == "POST" and row["op"] == "recovery"]

    def test_parser_aliases_strict_confirmation_and_legacy_play(self):
        for alias in ("recovery", "recover"):
            self.assertEqual(ott.parse_command(["fav", alias])[1], {"op": "recovery", "mode": "list"})
            self.assertEqual(ott.parse_command(["fav", alias, "inspect", OPERATION])[1]["mode"], "inspect")
        for alias in ("ack", "acknowledge"):
            self.assertEqual(ott.parse_command(["fav", alias, OPERATION, "--dry-run"])[1]["op"], "acknowledge")
            self.assertEqual(ott.parse_command(["fav", alias, OPERATION, "--confirm", PLAN])[1]["confirm"], PLAN)
        for words in (["ack", OPERATION], ["ack", OPERATION, "--confirm", PLAN, "--dry-run"],
                      ["ack", OPERATION + "\n", "--dry-run"], ["recovery", "nonsense"], ["ack", OPERATION, "--confirm", "f" * 63]):
            with self.assertRaises(ott.Error):
                ott.parse_command(["fav", *words])
        self.assertEqual(ott.parse_command(["fav", "play", "recovery"])[0], "favorites")

    def test_global_read_only_and_dry_run_without_catalog_or_local_directories(self):
        self.client.caps_change = lambda data: {key: value for key, value in data.items() if key != "favorites_transfer"}
        original = self.path.read_bytes()
        for words in (["recovery"], ["recover", OPERATION], ["ack", OPERATION, "--dry-run"]):
            result = self.call(words)
            self.assertIn(result["status"], ("recovery_list", "recovery_inspected", "planned"))
        self.assertEqual(self.client.posts(), ["capabilities", "recovery"] * 3)
        self.assertEqual(list(self.directory.iterdir()), [self.path])
        self.assertEqual(self.path.read_bytes(), original)

    def test_confirm_private_intent_then_exact_ack_preserves_unknown(self):
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "acknowledgement_confirmed")
        self.assertEqual(result["original_outcome"], "unknown")
        self.assertFalse(result["replay_allowed"])
        self.assertNotIn("verified", result)
        self.assertEqual(self.modes(), ["inspect", "acknowledge"])
        files = list(self.directory.glob(".favorites-operations-*/*.json"))
        self.assertEqual(len(files), 2)
        self.assertTrue(all("favorites-ack-" in p.name for p in files))
        self.assertTrue(all("completion" not in json.loads(p.read_bytes()) for p in files))
        if os.name != "nt":
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in files))
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            ott.print_favorites_command(result)
        self.assertIn("Original mutation outcome is unchanged", output.getvalue())

    def test_lost_or_invalid_ack_inspects_once_and_never_replays(self):
        for failure in ("lose_ack", "invalid_ack"):
            with self.subTest(failure=failure):
                self.setUp()
                setattr(self.client, failure, True)
                result = self.call(["ack", OPERATION, "--confirm", PLAN])
                self.assertEqual(result["status"], "acknowledgement_observed")
                self.assertEqual(self.modes(), ["inspect", "acknowledge", "inspect"])
                self.assertNotIn("prepare", self.client.posts())
                self.assertNotIn("commit", self.client.posts())

    def test_changed_confirmation_no_marker_or_multi_target_never_acknowledge(self):
        result = self.call(["ack", OPERATION, "--confirm", "f" * 64])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(list(self.directory.iterdir()), [self.path])
        with self.assertRaises(ott.Error):
            self.call(["ack", OPERATION, "--confirm", PLAN], targets=[("one", "one"), ("two", "two")])
        self.client.caps_change = lambda data: {key: value for key, value in data.items() if key != "favorites_transfer_recovery"}
        with self.assertRaises(ott.Error):
            self.call(["recovery"])
        self.assertNotIn("acknowledge", self.modes())

    def test_store_failure_or_deadline_before_ack_no_mutation(self):
        with mock.patch.object(commands._DurableContext, "save_acknowledgement", side_effect=OSError("private")):
            result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.modes(), ["inspect"])
        result = self.call(["ack", OPERATION, "--confirm", PLAN], timeout=6)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.modes(), ["inspect", "inspect"])

    def test_missing_and_legacy_records_are_inspect_only(self):
        self.client.inspect_mode = "legacy_inspect"
        self.assertEqual(self.call(["ack", OPERATION, "--dry-run"])["status"], "blocked")
        self.client.rows = False
        self.assertEqual(self.call(["ack", OPERATION, "--confirm", PLAN])["status"], "blocked")
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_readiness_probe_is_mandatory_before_prepare_and_read_only_collection_unchanged(self):
        self.client.documents["one"]["scope"] = copy.deepcopy(host.SCOPE)
        original_data = self.client.data
        for marker in (False, True):
            with self.subTest(marker=marker):
                self.client.calls.clear()
                self.client.caps_change = (lambda data: data) if marker else (
                    lambda data: {key: value for key, value in data.items() if key != "favorites_transfer_recovery"})
                self.client.data = lambda params: original_data(params)
                with self.assertRaises(ott.Error):
                    self.call(["clear"])
                self.assertNotIn("prepare", self.client.posts())
                self.assertNotIn("begin", self.client.posts())
                self.assertNotIn("commit", self.client.posts())
                self.assertEqual(self.client.posts().count("recovery"), int(marker))
        self.client.caps_change = lambda data: {key: value for key, value in data.items() if key != "favorites_transfer_recovery"}
        before = len(self.client.posts())
        result = self.call(["collect", "Home", "--dry-run"])
        self.assertEqual(result["status"], "planned")
        self.assertNotIn("recovery", self.client.posts()[before:])
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_old_controller_rejected_probe_never_downgrades_or_repeats(self):
        self.client.rows = False
        def reject(_api, _params):
            error = OSError("private")
            error.code = 400
            raise error
        self.client.post_effects["recovery"] = reject
        with self.assertRaises(ott.Error) as raised:
            self.call(["clear"])
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(self.modes(), ["list"])
        self.assertNotIn("prepare", self.client.posts())
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_new_explicit_confirmation_reuses_only_identical_immutable_intent(self):
        admin_dir = self.directory / "admin"
        admin_dir.mkdir(mode=0o700)
        store = operations.AcknowledgementStore(admin_dir, server=self.client.server, device="one")
        inspection, request = checked(), copy.deepcopy(FIX["acknowledge"]["request"])
        self.assertTrue(store.save_intent(inspection, request))
        content = next(admin_dir.glob("*.json")).read_bytes()
        second = protocol.validate_result(inspection.request, inspection.data, request_id="b" * 32,
                                          response_id="b" * 32, status="ok")
        self.assertFalse(store.save_intent(second, request))
        self.assertEqual(next(admin_dir.glob("*.json")).read_bytes(), content)
        peer = remote.RecoveryPeer(self.client, "one", runtime=RUNTIME,
            monotonic=self.clock, sleep=self.clock.sleep)
        result = peer.acknowledge(second, PLAN, store.save_intent)
        self.assertEqual(result["status"], "acknowledgement_confirmed")
        self.assertEqual(self.modes(), ["acknowledge"])
        self.assertEqual(self.client.effects, 1)
        store.save_evidence(second, request, result["evidence"])
        self.assertEqual(next(admin_dir.glob("*.intent.json")).read_bytes(), content)
        self.assertEqual(store.load_intent(request["acknowledgement_id"], OPERATION, PLAN)[0].request_id, "a" * 32)
        foreign = operations.AcknowledgementStore(admin_dir, server=self.client.server, device="two")
        with self.assertRaises(operations.OperationStoreError):
            foreign.save_intent(inspection, request)
        with self.assertRaises(operations.OperationStoreError):
            foreign.load_intent(request["acknowledgement_id"], OPERATION, PLAN)
        self.assertEqual(next(admin_dir.glob("*.intent.json")).read_bytes(), content)

    def test_crash_after_intent_before_post_allows_one_fresh_confirmed_attempt(self):
        durable = commands._DurableContext(self.path, self.client, "one")
        self.assertTrue(durable.save_acknowledgement(checked(), FIX["acknowledge"]["request"]))
        path = next(durable.admin.directory.glob("*.intent.json"))
        before = path.read_bytes()
        self.assertEqual(self.client.posts(), [])
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "acknowledgement_confirmed")
        self.assertEqual(result["warnings"], [])
        self.assertEqual((self.modes(), self.client.effects), (["inspect", "acknowledge"], 1))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), json.dumps(self.config).encode())

    def test_fresh_confirmation_after_lost_effect_observes_saved_audit_without_ack(self):
        self.client.lose_ack = True
        def missing(data):
            if data["mode"] == "inspect" and data["record"]["acknowledgement"]:
                data["record"]["acknowledgement"] = None
            return data
        self.client.mutators["recovery"] = missing
        first = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(first["status"], "unknown")
        intent = next(self.directory.glob(".favorites-operations-*/*.intent.json"))
        before = intent.read_bytes()
        self.client.mutators.clear()
        self.client.calls.clear()
        second = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(second["status"], "acknowledgement_observed")
        self.assertEqual((self.modes(), self.client.effects), (["inspect"], 1))
        self.assertEqual(second["original_outcome"], "unknown")
        self.assertNotIn("verified", second)
        self.assertEqual(intent.read_bytes(), before)
        self.assertEqual(second["warnings"], [])
        self.assertEqual(self.call(["ack", OPERATION, "--confirm", "f" * 64])["status"], "blocked")
        self.assertEqual(self.modes(), ["inspect", "inspect"])

    def test_older_queued_ack_wins_cas_and_fresh_manual_attempt_has_one_effect(self):
        durable = commands._DurableContext(self.path, self.client, "one")
        durable.save_acknowledgement(checked(), FIX["acknowledge"]["request"])
        intent = next(durable.admin.directory.glob("*.intent.json"))
        before = intent.read_bytes()
        self.client.older_ack_race = True
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "acknowledgement_observed")
        self.assertEqual((self.modes(), self.client.effects), (["inspect", "acknowledge", "inspect"], 1))
        self.assertEqual(result["warnings"], [])
        self.assertEqual(intent.read_bytes(), before)
        self.assertNotIn("commit", self.client.posts())
        self.assertNotIn("prepare", self.client.posts())

    def test_stored_intent_context_mismatch_or_busy_inspect_never_sends_ack(self):
        durable = commands._DurableContext(self.path, self.client, "one")
        durable.save_acknowledgement(checked(), FIX["acknowledge"]["request"])
        path = next(durable.admin.directory.glob("*.intent.json"))
        before = path.read_bytes()
        def changed(data):
            if data["mode"] == "inspect":
                # The same digest tuple cannot bless changed public evidence.
                data["record"]["original_state"] = "unknown"
            return data
        self.client.mutators["recovery"] = changed
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual((result["status"], result["reason"]), ("blocked", "context_not_saved"))
        self.assertEqual(self.modes(), ["inspect"])
        self.assertEqual(path.read_bytes(), before)
        self.client.mutators["recovery"] = lambda _data: {
            "version": 1, "runtime": RUNTIME, "op": "recovery", "error": "busy"}
        with self.assertRaises(ott.Error):
            self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(self.modes(), ["inspect", "inspect"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.client.effects, 0)

    def test_already_acknowledged_without_matching_private_intent_is_read_only(self):
        self.client.acked = True
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.modes(), ["inspect"])
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_bare_player_help_exposes_independent_recovery_before_plain_summary(self):
        caps = {"version": 1, "player": {"version": "test", "platform": "tauri", "runtime": RUNTIME},
                "lifecycle": [], "input": [], "playback": [], "favorites_transfer_recovery": {"version": 1}}
        client = SimpleNamespace(config={"players": {"t1": "one"}}, timeout=45, device=lambda name: "one", on_receipt=None)
        client.call = mock.Mock(side_effect=[{"provider": "m3u", "volume": 35, "playing": True}, caps])
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=client.config), mock.patch.object(ott, "Client", return_value=client), \
                mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
            code = ott.main(["t1"])
        self.assertEqual((code, errors.getvalue()), (0, ""))
        text = output.getvalue()
        self.assertIn("ott t1 fav recovery", text)
        self.assertIn("ott t1 fav ack OPERATION_UUID --dry-run / --confirm PLAN_SHA256", text)
        self.assertNotIn("CURRENT VERIFIED PLEX ONLY", text)
        self.assertLess(text.index("Available controls now:"), text.index("Player summary:"))
        self.assertLess(text.index("fav recovery"), text.index("Player summary:"))
        self.assertEqual(client.call.call_args_list, [mock.call("one", "status", {}), mock.call("one", "capabilities", {})])
        self.assertNotIn('"provider"', text)

    def test_local_audit_error_preserves_player_admin_confirmation(self):
        with mock.patch.object(operations.AcknowledgementStore, "save_evidence", side_effect=OSError("private")):
            result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "acknowledgement_confirmed")
        self.assertEqual(result["warnings"], ["local_acknowledgement_evidence_unconfirmed"])
        self.assertEqual(result["original_outcome"], "unknown")
        self.assertEqual(self.modes(), ["inspect", "acknowledge"])

    def test_ack_observed_after_other_ledger_changes_is_not_historical_after_image_verification(self):
        self.client.lose_ack = True
        def changed(data):
            if data["mode"] == "inspect" and data["record"]["acknowledgement"]:
                data["ledger_revision"] = "e" * 64
            return data
        self.client.mutators["recovery"] = changed
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "acknowledgement_observed")
        self.assertEqual(result["evidence"]["ledger_revision"], "e" * 64)
        self.assertNotIn("verified", result)

    def test_foreign_or_missing_observed_audit_never_confirms(self):
        self.client.lose_ack = True
        def remove(data):
            if data["mode"] == "inspect" and data["record"]["acknowledgement"]:
                data["record"]["acknowledgement"] = None
            return data
        self.client.mutators["recovery"] = remove
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.modes(), ["inspect", "acknowledge", "inspect"])

    def test_admin_intent_does_not_modify_original_unknown_context(self):
        private = commands._DurableContext(self.path, self.client, "one")
        private._ensure()
        original = private.store.directory / (operations.PREFIX + OPERATION + ".json")
        original.write_bytes(b'{"synthetic":"original-unknown-audit"}')
        if os.name != "nt":
            original.chmod(0o600)
        before = original.read_bytes()
        result = self.call(["ack", OPERATION, "--confirm", PLAN])
        self.assertEqual(result["status"], "acknowledgement_confirmed")
        self.assertEqual(original.read_bytes(), before)

    def test_group_entrypoint_rejects_ack_before_host_and_unknown_exits_one(self):
        group_args = SimpleNamespace(words=["one,two", "fav", "ack", OPERATION, "--confirm", PLAN])
        with mock.patch.object(ott, "favorites_host_command") as handler, self.assertRaises(ott.Error):
            ott.player_group_command(self.client, self.config, [("one", "one"), ("two", "two")], group_args, [])
        handler.assert_not_called()
        args = SimpleNamespace(config=str(self.path), timeout=45, json=True)
        for status in ("unknown", "blocked", "acknowledgement_observed", "acknowledgement_confirmed"):
            fake = SimpleNamespace(run=lambda *a, state=status: {"op": "acknowledge", "status": state, "replay_allowed": False})
            with mock.patch.object(ott, "favorites_module", return_value=fake), mock.patch("sys.stdout", io.StringIO()):
                code = ott.favorites_host_command(self.client, self.config, args, [("one", "one")], {"op": "acknowledge"})
            self.assertEqual(code, 1 if status in ("unknown", "blocked") else 0)


if __name__ == "__main__":
    unittest.main()
