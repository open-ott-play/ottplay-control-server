"""Durable intent evidence, only synthetic private temporary directories."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    spec = importlib.util.spec_from_file_location("favorites_operations_tested", ROOT / "cli/favorites_operations.py")
    operations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operations)


def context(number=1):
    return {"operation_id": f"00000000-0000-4000-8000-{number:012x}", "version": 1,
            "begin": {"runtime": "fixture-runtime"}, "preview": {"plan_digest": "a" * 64}}


def validate(value):
    if set(value) != {"operation_id", "version", "begin", "preview"} or value["version"] != 1:
        raise ValueError("fixture-secret-must-not-leak")
    return value


class FavoritesOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.store = self.create()

    def create(self, **kwargs):
        return operations.ContextStore(self.directory, **({"server": "https://fixture.invalid/control",
            "device": "fixture-device", "validate_context": validate} | kwargs))

    def error(self, code, callback, *args):
        with self.assertRaises(operations.OperationStoreError) as caught:
            callback(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_save_reload_idempotence_target_binding_and_private_files(self):
        intent = context()
        result = self.store.save(intent)
        self.assertEqual(result["player_outcome"], "unknown")
        self.assertEqual(self.create().load(intent["operation_id"]), intent)
        first = Path(result["path"]).read_bytes()
        self.assertEqual(self.store.save(copy.deepcopy(intent)), result)
        self.assertEqual(Path(result["path"]).read_bytes(), first)
        self.assertNotIn(b"fixture.invalid", first)
        self.assertNotIn(b"fixture-device", first)
        self.error("wrong_target", self.create(device="other-device").load, intent["operation_id"])
        self.error("wrong_target", self.create(server="https://elsewhere.invalid").load, intent["operation_id"])
        if os.name != "nt":
            self.assertEqual(Path(result["path"]).stat().st_mode & 0o777, 0o600)

    def test_different_context_cannot_replace_retained_intent(self):
        original = context()
        result = self.store.save(original)
        changed = copy.deepcopy(original)
        changed["preview"]["plan_digest"] = "b" * 64
        self.error("conflict", self.store.save, changed)
        self.assertEqual(self.store.load(original["operation_id"]), original)
        self.assertEqual(len(list(self.directory.glob("*.json"))), 1)
        self.assertTrue(Path(result["path"]).is_file())

    def test_missing_or_invalid_context_never_means_not_applied(self):
        self.error("missing", self.store.load, context()["operation_id"])
        self.error("invalid_context", self.store.save, context() | {"token": "fixture-secret"})
        for value in ("../elsewhere", True, None, "00000000-0000-5000-8000-000000000001"):
            self.error("invalid_context", self.store.load, value)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_capacity_refuses_without_eviction(self):
        with mock.patch.object(operations, "MAX_RECORDS", 2):
            one = self.store.save(context(1))
            two = self.store.save(context(2))
            self.error("capacity", self.store.save, context(3))
            self.assertEqual(self.store.save(context(1)), one)
            self.assertTrue(Path(two["path"]).exists())
        with mock.patch.object(operations, "MAX_TOTAL_BYTES", 1):
            self.error("capacity", self.store.save, context(4))
        self.assertEqual(len(list(self.directory.glob("*.json"))), 2)

    def test_sync_failure_refuses_commit_authorization_and_is_rechecked(self):
        with mock.patch.object(operations.private_files._Parent, "sync", side_effect=OSError("fixture-secret")):
            self.error("unavailable", self.store.save, context())
            self.error("unavailable", self.store.save, context())
        self.assertEqual(self.store.load(context()["operation_id"]), context())
        self.assertEqual(self.store.save(context())["player_outcome"], "unknown")

    def test_corrupt_or_oversized_retained_evidence_refused(self):
        result = self.store.save(context())
        path = Path(result["path"])
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'"version":1', b'"version":1,"version":1'))
        self.error("unavailable", self.store.load, context()["operation_id"])
        path.write_bytes(raw)
        with mock.patch.object(operations, "MAX_RECORD_BYTES", 32):
            self.error("invalid_context", self.store.load, context()["operation_id"])
        value = json.loads(raw)
        value["context"]["operation_id"] = context(2)["operation_id"]
        path.write_text(json.dumps(value))
        self.error("invalid_context", self.store.load, context()["operation_id"])

    @unittest.skipIf(os.name == "nt", "POSIX private file and link checks")
    def test_symlink_hardlink_and_nonprivate_directory_refused(self):
        original = self.store.save(context())
        path = Path(original["path"])
        os.link(path, self.directory / "other-link")
        self.error("unavailable", self.store.load, context()["operation_id"])
        (self.directory / "other-link").unlink()
        path.unlink()
        path.symlink_to(self.directory / "outside")
        self.error("unavailable", self.store.save, context())
        path.unlink()
        self.directory.chmod(0o755)
        self.error("unavailable", self.store.save, context())

    def test_validator_cannot_silently_change_saved_evidence(self):
        store = self.create(validate_context=lambda value: value | {"extra": True})
        self.error("invalid_context", store.save, context())
        self.assertEqual(list(self.directory.iterdir()), [])
