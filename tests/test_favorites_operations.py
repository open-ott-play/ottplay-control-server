"""Durable intent evidence, only synthetic private temporary directories."""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
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


def real_evidence(number=1, operation="commit"):
    fixtures = json.loads((ROOT / "contracts/favorites-transfer-v1.json").read_bytes())["fixtures"]
    operation_id = context(number)["operation_id"]
    transaction_id = f"10000000-0000-4000-8000-{number:012x}"

    def identifiers(value):
        if type(value) is dict:
            return {key: (operation_id if key == "operation_id" else transaction_id if key == "transaction_id"
                          else identifiers(item)) for key, item in value.items()}
        if type(value) is list:
            return [identifiers(item) for item in value]
        return value

    fixtures = identifiers(fixtures)

    def result(name, retained=None):
        fixture = fixtures[name]
        return operations.protocol.validate_result(fixture["request"], fixture["response"], request_id="1" * 32,
            response_id="1" * 32, status="ok", context=retained)

    record = operations.remote.recovery_record(result("begin"), result("preview"), fixtures["commit"]["request"])
    return record, result(operation, operations.remote.restore_context(record))


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

    def real_store(self, **kwargs):
        return self.create(validate_context=operations.remote.validate_recovery_record, **kwargs)

    def complete(self, store, number):
        intent, result = real_evidence(number)
        saved = store.save(intent)
        store.mark_completed(intent["operation_id"], result)
        return intent, result, Path(saved["path"])

    def test_completion_is_revalidated_and_idempotent_save_preserves_proof(self):
        store = self.real_store()
        intent, result, path = self.complete(store, 1)
        raw, modified = path.read_bytes(), path.stat().st_mtime_ns
        receipt = store.mark_completed(intent["operation_id"], result)
        self.assertEqual(receipt["retention"], "completed")
        self.assertFalse(receipt["replay_allowed"])
        # A status receipt with the same claims also preserves the original proof/time.
        _, status = real_evidence(1, "status")
        store.mark_completed(intent["operation_id"], status)
        store.save(intent)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (raw, modified))
        self.assertEqual(store.load(intent["operation_id"]), intent)
        self.assertIn("completion", json.loads(raw))
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_more_than_32_completed_operations_preserve_pending_and_recent_evidence(self):
        store = self.real_store()
        protected = []
        for number in (100, 101):
            intent, _ = real_evidence(number)
            saved = store.save(intent)
            protected.append((Path(saved["path"]), Path(saved["path"]).read_bytes()))
        for number in range(1, 43):
            self.complete(store, number)
            paths = list(self.directory.glob("*.json"))
            self.assertLessEqual(len(paths), operations.MAX_RECORDS)
            self.assertLessEqual(sum(path.stat().st_size for path in paths), operations.MAX_TOTAL_BYTES)
        for path, raw in protected:
            self.assertEqual(path.read_bytes(), raw)
        self.error("missing", store.load, context(1)["operation_id"])
        for number in range(35, 43):
            self.assertEqual(store.load(context(number)["operation_id"]), real_evidence(number)[0])

    def test_completion_boolean_forgery_wrong_context_and_foreign_target_cannot_free_records(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        before = path.read_bytes()
        self.error("invalid_completion", store.mark_completed, intent["operation_id"], True)
        self.error("invalid_completion", store.mark_completed, intent["operation_id"], real_evidence(2)[1])
        forged = result.data
        forged["document_sha256"] = forged["receipt"]["document_sha256"] = "0" * 64
        self.error("invalid_completion", store.mark_completed, intent["operation_id"],
                   replace(result, _data=operations._encoded(forged), verified=True))
        self.error("wrong_target", self.real_store(device="another-device").mark_completed,
                   intent["operation_id"], result)
        self.assertEqual(path.read_bytes(), before)
        with mock.patch.object(operations, "MAX_RECORDS", 1):
            self.error("capacity", store.save, real_evidence(2)[0])

    def test_partial_unknown_and_cross_runtime_completions_stay_protected(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        before = path.read_bytes()
        for outcome in ("partial", "unknown"):
            data = result.data
            data.pop("receipt")
            data.pop("counts")
            data.pop("document_sha256")
            data.pop("plan_digest")
            data.pop("new_revision")
            data["outcome"] = outcome
            data["per_store"][0]["outcome"] = "verified" if outcome == "partial" else "unknown"
            data["per_store"][1]["outcome"] = "not_applied"
            unconfirmed = operations.protocol.validate_result(result.request, data, request_id=result.request_id,
                response_id=result.request_id, status="ok", context=operations.remote.restore_context(intent))
            self.assertFalse(unconfirmed.verified)
            self.error("invalid_completion", store.mark_completed, intent["operation_id"],
                       replace(unconfirmed, verified=True))
        _, status = real_evidence(1, "status")
        request, data = status.request, status.data
        request["runtime"] = data["runtime"] = "fresh-runtime"
        self.error("invalid_completion", store.mark_completed, intent["operation_id"],
                   replace(status, _request=operations._encoded(request), _data=operations._encoded(data)))
        self.assertEqual(path.read_bytes(), before)

    def test_tampered_foreign_and_interrupted_completion_metadata_never_authorize_eviction(self):
        store = self.real_store()
        _, _, damaged = self.complete(store, 1)
        value = json.loads(damaged.read_bytes())
        value["completion"]["response"]["data"]["receipt"]["plan_digest"] = "0" * 64
        damaged.write_bytes(operations._encoded(value))
        _, _, foreign = self.complete(self.real_store(device="other-device"), 2)
        intent, _ = real_evidence(3)
        interrupted = Path(store.save(intent)["path"])
        value = json.loads(interrupted.read_bytes())
        value["completion"] = {"version": 1}
        interrupted.write_bytes(operations._encoded(value))
        originals = {path: path.read_bytes() for path in (damaged, foreign, interrupted)}
        with mock.patch.object(operations, "MAX_RECORDS", 3):
            self.error("capacity", store.save, real_evidence(4)[0])
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)

    def test_byte_pressure_reclaims_without_enforcing_a_minimum_of_eight(self):
        store = self.real_store()
        _, _, old = self.complete(store, 1)
        intent, result = real_evidence(2)
        pending = Path(store.save(intent)["path"])
        provisional = json.loads(pending.read_bytes())
        provisional["completion"] = {"version": 1, "recorded_ns": 2**62, "request": result.request,
            "response": {"id": result.request_id, "status": result.status, "data": result.data}}
        peak_without_old = pending.stat().st_size + len(operations._encoded(provisional))
        with mock.patch.object(operations, "MAX_TOTAL_BYTES", peak_without_old):
            store.mark_completed(intent["operation_id"], result)
        self.assertFalse(old.exists())
        self.assertIn("completion", json.loads(pending.read_bytes()))

    def test_incomplete_temporary_counts_toward_peak_and_is_never_deleted_as_evidence(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        before = path.read_bytes()
        interrupted = self.directory / (".favorites-completion-" + "a" * 32 + ".tmp")
        interrupted.write_bytes(b"partial-private-write")
        interrupted.chmod(0o600)
        with mock.patch.object(operations, "MAX_TOTAL_BYTES", len(before) + interrupted.stat().st_size):
            self.error("capacity", store.mark_completed, intent["operation_id"], result)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(interrupted.read_bytes(), b"partial-private-write")

    def test_zero_byte_temporary_count_reserves_new_completion_before_write(self):
        store = self.real_store()
        interrupted = []
        for number in range(operations.MAX_TEMPORARY_FILES):
            path = self.directory / f".favorites-completion-{number:032x}.tmp"
            path.touch(mode=0o600)
            interrupted.append(path)
        intent, result = real_evidence(1)
        # Save does not allocate another completion temporary; exactly 8 fit.
        record = Path(store.save(intent)["path"])
        before = record.read_bytes()
        with mock.patch.object(operations.private_files._Parent, "write_new") as write:
            self.error("capacity", store.mark_completed, intent["operation_id"], result)
            write.assert_not_called()
        self.assertEqual(record.read_bytes(), before)
        self.assertTrue(all(path.exists() and path.stat().st_size == 0 for path in interrupted))
        # Seven interrupted files leave room for the eighth in-flight file.
        interrupted.pop().unlink()  # Test fixture cleanup, never Store recovery.
        store.mark_completed(intent["operation_id"], result)
        self.assertEqual(set(self.directory.glob(".favorites-completion-*.tmp")), set(interrupted))
        for number in range(operations.MAX_TEMPORARY_FILES, operations.MAX_TEMPORARY_FILES + 2):
            path = self.directory / f".favorites-completion-{number:032x}.tmp"
            path.touch(mode=0o600)
            interrupted.append(path)
        # Nine pre-existing empty files also refuse new save admission.
        with mock.patch.object(operations.private_files._Parent, "write_new") as write:
            self.error("capacity", store.save, real_evidence(2)[0])
            write.assert_not_called()
        self.assertEqual(set(self.directory.glob(".favorites-completion-*.tmp")), set(interrupted))
        self.error("missing", store.load, context(2)["operation_id"])

    def test_completion_staging_failure_preserves_original_context(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        before = path.read_bytes()
        with mock.patch.object(operations.private_files._Parent, "write_new", side_effect=OSError("fixture-secret")):
            self.error("unavailable", store.mark_completed, intent["operation_id"], result)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".favorites-completion-*.tmp")), [])

    def test_atomic_replace_and_readback_failures_report_uncertain_without_rollback(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        before = path.read_bytes()
        original_replace = operations.private_files._Parent.replace

        def fail_after_replace(parent, source, destination):
            original_replace(parent, source, destination)
            raise OSError("fixture-secret")

        with mock.patch.object(operations.private_files._Parent, "replace", side_effect=OSError("fixture-secret")):
            self.error("retention_uncertain", store.mark_completed, intent["operation_id"], result)
        self.assertEqual(path.read_bytes(), before)
        with mock.patch.object(operations.private_files._Parent, "replace", fail_after_replace):
            self.error("retention_uncertain", store.mark_completed, intent["operation_id"], result)
        self.assertIn("completion", json.loads(path.read_bytes()))
        # Explicit verification is local evidence recovery, never player replay.
        self.assertEqual(store.mark_completed(intent["operation_id"], result)["retention"], "completed")

    def test_directory_fsync_failure_after_replace_preserves_uncertain_evidence(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        with mock.patch.object(operations.private_files._Parent, "sync", side_effect=OSError("fixture-secret")):
            self.error("retention_uncertain", store.mark_completed, intent["operation_id"], result)
        self.assertIn("completion", json.loads(path.read_bytes()))
        self.assertEqual(store.load(intent["operation_id"]), intent)

    def test_file_fsync_failure_and_truncated_readback_do_not_claim_completion(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        before = path.read_bytes()
        with mock.patch.object(operations.os, "fsync", side_effect=OSError("fixture-secret")):
            self.error("unavailable", store.mark_completed, intent["operation_id"], result)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".favorites-completion-*.tmp")), [])
        original_replace = operations.private_files._Parent.replace

        def truncate_after_replace(parent, source, destination):
            original_replace(parent, source, destination)
            path.write_bytes(b'{"version":1,"completion":')

        with mock.patch.object(operations.private_files._Parent, "replace", truncate_after_replace):
            self.error("retention_uncertain", store.mark_completed, intent["operation_id"], result)
        self.assertEqual(path.read_bytes(), b'{"version":1,"completion":')
        with mock.patch.object(operations, "MAX_RECORDS", 1):
            self.error("capacity", store.save, real_evidence(2)[0])

    def test_cleanup_does_not_delete_a_replacement_parent_file(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        store.save(intent)
        original_write = operations.private_files._Parent.write_new
        # Keep the original private directory available for TemporaryDirectory cleanup.
        renamed = self.directory.with_name(self.directory.name + "-retained")
        self.addCleanup(lambda: shutil.rmtree(renamed, ignore_errors=True))
        replacement = []

        def swap_after_write(parent, name, raw):
            original_write(parent, name, raw)
            if name.startswith(".favorites-completion-"):
                self.directory.rename(renamed)
                self.directory.mkdir(mode=0o700)
                other = self.directory / name
                other.write_bytes(b"unrelated-private-evidence")
                other.chmod(0o600)
                replacement.append(other)

        with mock.patch.object(operations.private_files._Parent, "write_new", swap_after_write):
            self.error("unavailable", store.mark_completed, intent["operation_id"], result)
        self.assertEqual(replacement[0].read_bytes(), b"unrelated-private-evidence")

    def test_noncooperating_edit_before_replace_is_preserved(self):
        store = self.real_store()
        intent, result = real_evidence(1)
        path = Path(store.save(intent)["path"])
        changed = path.read_bytes() + b" "
        original_write = operations.private_files._Parent.write_new

        def edit_before_replace(parent, name, raw):
            original_write(parent, name, raw)
            if name.startswith(".favorites-completion-"):
                path.write_bytes(changed)

        with mock.patch.object(operations.private_files._Parent, "write_new", edit_before_replace):
            self.error("conflict", store.mark_completed, intent["operation_id"], result)
        self.assertEqual(path.read_bytes(), changed)
        self.assertEqual(list(self.directory.glob(".favorites-completion-*.tmp")), [])

    def test_reclaim_sync_failure_does_not_remove_pending_or_admit_next_record(self):
        store = self.real_store()
        _, _, completed = self.complete(store, 1)
        intent, _ = real_evidence(2)
        pending = Path(store.save(intent)["path"])
        before = pending.read_bytes()
        with mock.patch.object(operations, "MAX_RECORDS", 2), mock.patch.object(
                operations.private_files._Parent, "sync", side_effect=OSError("fixture-secret")):
            self.error("retention_uncertain", store.save, real_evidence(3)[0])
        self.assertFalse(completed.exists())
        self.assertEqual(pending.read_bytes(), before)
        self.error("missing", store.load, context(3)["operation_id"])
