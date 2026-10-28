"""Global audit evidence uses real private files with synthetic metadata only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
import favorites_operations as old
import global_favorites_operations as operations

FIXTURE = json.loads((ROOT / "docs/design/global-favorites-recovery-v1.fixtures.json").read_bytes())["positive"]


class GlobalAuditStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = operations.AcknowledgementStore(self.directory, server="https://controller.invalid", device="test-device")
        self.inspection = copy.deepcopy(FIXTURE["inspect_response"])
        self.request = copy.deepcopy(FIXTURE["acknowledge_request"])

    def load(self, store=None):
        return (store or self.store).load_intent(self.request["acknowledgement_id"],
            self.request["operation_id"], self.request["plan_sha256"])

    def test_exact_intent_roundtrip_and_idempotent_private_retention(self):
        self.assertTrue(self.store.save_intent(self.inspection, self.request))
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        self.assertFalse(self.store.save_intent(self.inspection, self.request))
        self.assertEqual(self.load(), (self.inspection, self.request))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_target_or_plan_cannot_borrow_another_intent(self):
        self.store.save_intent(self.inspection, self.request)
        other = operations.AcknowledgementStore(self.directory, server="https://controller.invalid", device="other-device")
        with self.assertRaises(old.OperationStoreError):
            self.load(other)
        self.request["plan_sha256"] = "0" * 64
        with self.assertRaises(old.OperationStoreError):
            self.load()

    def test_invalid_confirmation_tuple_is_not_persisted(self):
        self.request["plan_sha256"] = "0" * 64
        with self.assertRaises(old.OperationStoreError):
            self.store.save_intent(self.inspection, self.request)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_confirmation_must_be_literal_boolean_true(self):
        self.request["accept_unknown"] = 1
        with self.assertRaises(old.OperationStoreError):
            self.store.save_intent(self.inspection, self.request)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_saved_wrapper_version_must_be_integer_one(self):
        self.store.save_intent(self.inspection, self.request)
        path = next(self.directory.glob("*.intent.json"))
        saved = json.loads(path.read_bytes())
        saved["version"] = True
        path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(old.OperationStoreError):
            self.load()

    def test_exact_audit_evidence_preserves_original_unknown(self):
        self.store.save_intent(self.inspection, self.request)
        evidence = copy.deepcopy(FIXTURE["acknowledge_response"])
        self.store.save_evidence(self.inspection, self.request, evidence)
        retained = json.loads(next(self.directory.glob("*.evidence.json")).read_bytes())
        self.assertEqual(retained["classification"], "administrative-audit-only")
        self.assertEqual(retained["evidence"]["record"]["original_outcome"], "unknown")
        self.assertEqual(self.load(), (self.inspection, self.request))

    def test_changed_original_evidence_is_not_a_valid_audit(self):
        self.store.save_intent(self.inspection, self.request)
        evidence = copy.deepcopy(FIXTURE["acknowledge_response"])
        evidence["record"]["original_entry_sha256"] = "0" * 64
        with self.assertRaises(old.OperationStoreError):
            self.store.save_evidence(self.inspection, self.request, evidence)
        self.assertEqual(list(self.directory.glob("*.evidence.json")), [])

    def test_legacy_codec_does_not_accept_global_intent(self):
        self.store.save_intent(self.inspection, self.request)
        legacy = old.AcknowledgementStore(self.directory, server="https://controller.invalid", device="test-device")
        with self.assertRaises(old.OperationStoreError):
            self.load(legacy)


if __name__ == "__main__":
    unittest.main()
