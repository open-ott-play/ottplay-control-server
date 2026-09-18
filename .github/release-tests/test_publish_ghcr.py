"""Offline project GHCR trust-boundary and no-rebuild publication contracts."""

import copy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import publish_ghcr as publish
import release_control as rc
from version_plan import create_plan, plan_digest


class FakeGitHub:
    repo = publish.REPOSITORY

    def __init__(self):
        self.sha = "a" * 40
        self.policy = json.loads((ROOT / ".release-policy.json").read_text())
        self.plan = create_plan("0.1.0", "beta", 1, self.sha, self.policy)
        self.tag = self.plan["tag"]
        self.snapshot = {"schema": 1, "path": ".release-policy.json", "git_blob_sha": "b" * 40, "sha256": "c" * 64, "data": self.policy}
        self.payload = b"verified OCI fixture bytes"
        self.asset = {"name": publish.ARCHIVE, "size": len(self.payload), "sha256": rc.digest(self.payload)}
        self.manifest = {"schema": 1, "repository": self.repo, "version": "0.1.0", "channel": "beta", "tag": self.tag, "source_sha": self.sha, "source_policy": self.snapshot, "workflow_path": rc.WORKFLOW, "run_id": 123, "run_attempt": 1, "assets": [self.asset], "version_plan": self.plan, "plan_sha256": plan_digest(self.plan), "build_receipts": []}
        self.ref = {"ref": "refs/tags/" + self.tag, "object": {"type": "commit", "sha": self.sha}}
        self.release = {"id": 1, "tag_name": self.tag, "draft": False, "prerelease": True}
        self.run = {"id": 123, "repository": {"full_name": self.repo}, "head_repository": {"full_name": self.repo}, "head_sha": self.sha, "path": rc.WORKFLOW, "head_branch": "main", "event": "push", "run_attempt": 1, "status": "completed", "conclusion": "success"}
        self.gates = [{"name": "Release gate", "status": "completed", "conclusion": "success", "head_sha": self.sha}]
        self.raw = json.dumps(self.manifest).encode()
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as output:
            output.writestr(rc.MANIFEST, self.raw)
        self.archive = zipped.getvalue()
        self.artifacts = [{"id": 4, "name": "release-evidence", "expired": False, "digest": "sha256:" + rc.digest(self.archive), "workflow_run": {"id": 123, "head_sha": self.sha}}]
        self.assets = [{"id": 2, "name": rc.MANIFEST, "state": "uploaded"}, {"id": 3, "name": publish.ARCHIVE, "state": "uploaded", "digest": "sha256:" + rc.digest(self.payload)}]

    def api(self, path):
        if path == "":
            return {"full_name": self.repo, "default_branch": "main"}
        if path == "actions/runs/123":
            return self.run
        if path.startswith("git/ref/tags/"):
            return self.ref
        if path.startswith("releases/tags/"):
            return self.release
        raise AssertionError(path)

    def pages(self, path, field=None):
        if path == "releases/1/assets":
            return self.assets
        if path == "actions/runs/123/artifacts":
            return self.artifacts
        if path == "actions/runs/123/attempts/1/jobs":
            return self.gates
        if path == "releases":
            return []
        raise AssertionError(path)

    def binary(self, path):
        return {"releases/assets/2": self.raw, "releases/assets/3": self.payload, "actions/artifacts/4/zip": self.archive}[path]


class VerificationTests(unittest.TestCase):
    def verify(self, gh):
        # Payload-type and receipt internals have the vendored toolkit's own
        # independent suites; these stubs isolate this adapter's trust boundary.
        with tempfile.TemporaryDirectory() as temporary, patch.object(rc, "source_policy_snapshot", return_value=gh.snapshot), patch.object(publish, "verify_receipts", return_value=[]) as receipts, patch.object(publish, "verify_declared_artifacts") as declared:
            result = publish.verify_release(gh, gh.tag, Path(temporary))
            receipts.assert_called_once()
            declared.assert_called_once()
            self.assertEqual((Path(temporary) / publish.ARCHIVE).read_bytes(), gh.payload)
            return result

    def test_valid_beta_uses_frozen_plan_evidence_receipts_and_payload(self):
        gh = FakeGitHub()
        self.assertEqual(self.verify(gh), gh.manifest)
        self.assertEqual(publish.tag_for_run(gh, 123), gh.tag)

    def test_rc_and_stable_keep_the_toolkit_promotion_verifier(self):
        gh = FakeGitHub()
        plan = create_plan("0.1.0", "rc", 1, gh.sha, gh.policy)
        manifest = {**gh.manifest, "channel": "rc", "tag": plan["tag"], "version_plan": plan, "plan_sha256": plan_digest(plan)}
        self.assertEqual(publish.validate_candidate(json.dumps(manifest).encode(), plan["tag"]), manifest)
        def verified(_gh, tag, directory):
            self.assertEqual(tag, "v0.1.0")
            (directory / publish.ARCHIVE).write_bytes(gh.payload)
            return manifest
        with tempfile.TemporaryDirectory() as temporary, patch.object(publish, "verified_assets", side_effect=verified) as stable, patch.object(publish, "verify_receipts", return_value=[]), patch.object(publish, "verify_declared_artifacts"):
            self.assertEqual(publish.verify_release(gh, "v0.1.0", Path(temporary)), manifest)
            stable.assert_called_once()

    def test_destination_policy_and_receipt_coverage_cannot_be_bypassed(self):
        gh = FakeGitHub()
        wrong = copy.deepcopy(gh.manifest)
        wrong["source_policy"]["data"]["container_assets"] = {publish.ARCHIVE: "ghcr.io/attacker/repo"}
        with tempfile.TemporaryDirectory() as temporary, patch.object(publish, "verified_assets", return_value=wrong), self.assertRaises(rc.ReleaseError):
            publish.verify_release(gh, "v0.1.0", Path(temporary))
        with tempfile.TemporaryDirectory() as temporary, patch.object(publish, "verified_assets", return_value=gh.manifest), patch.object(publish, "verify_receipts", side_effect=ValueError("missing coverage")), self.assertRaises(ValueError):
            publish.verify_release(gh, "v0.1.0", Path(temporary))

    def test_manifest_identity_and_plan_tampering_fail(self):
        gh = FakeGitHub()
        changes = [("repository", "other/repo"), ("tag", "v0.1.0-beta.2"), ("source_sha", "b" * 40), ("run_id", True), ("plan_sha256", "0" * 64), ("workflow_path", ".github/workflows/evil.yml")]
        for field, value in changes:
            with self.subTest(field=field), self.assertRaises((rc.ReleaseError, ValueError)):
                m = copy.deepcopy(gh.manifest)
                m[field] = value
                publish.validate_candidate(json.dumps(m).encode(), gh.tag)
        for tag in ("latest", "v0.1.0-nightly.1", "v0.1.0-beta.1\n", "--help"):
            with self.assertRaises(rc.ReleaseError):
                publish.validate_candidate(gh.raw, tag)

    def test_ref_run_inventory_evidence_and_bytes_fail_closed(self):
        def ref(gh): gh.ref["object"]["sha"] = "b" * 40
        def repo(gh): gh.run["head_repository"]["full_name"] = "attacker/fork"
        def branch(gh): gh.run["head_branch"] = "untrusted"
        def event(gh): gh.run["event"] = "pull_request"
        def run(gh): gh.run["conclusion"] = "failure"
        def gate(gh): gh.gates[0]["conclusion"] = "skipped"
        def attempt(gh): gh.run["run_attempt"] = 2
        def digest(gh): gh.artifacts[0]["digest"] = "sha256:" + "0" * 64
        def expired(gh): gh.artifacts[0]["expired"] = True
        def bytes_changed(gh): gh.payload += b"changed"
        def inventory(gh): gh.assets.append({"id": 9, "name": "extra.txt", "state": "uploaded"})
        def asset_digest(gh): gh.assets[1]["digest"] = "sha256:" + "0" * 64
        def policy(gh): gh.snapshot = copy.deepcopy(gh.snapshot); gh.snapshot["data"]["notes"] = ["changed"]
        for mutation in (ref, repo, branch, event, run, gate, attempt, digest, expired, bytes_changed, inventory, asset_digest, policy):
            gh = FakeGitHub()
            mutation(gh)
            with self.subTest(mutation=mutation.__name__), self.assertRaises((rc.ReleaseError, ValueError)):
                self.verify(gh)

    def test_trigger_rejects_fork_and_different_immutable_artifact(self):
        gh = FakeGitHub()
        gh.run["head_repository"]["full_name"] = "fork/repo"
        with self.assertRaises(rc.ReleaseError): publish.tag_for_run(gh, 123)
        gh = FakeGitHub()
        gh.artifacts[0]["workflow_run"]["head_sha"] = "b" * 40
        with self.assertRaises(rc.ReleaseError): publish.tag_for_run(gh, 123)


class RegistryTests(unittest.TestCase):
    def call(self, outputs, execute=True):
        def completed(args, **kwargs):
            item = outputs.pop(0)
            return subprocess.CompletedProcess(args, *item)
        with patch.object(publish.subprocess, "run", side_effect=completed) as process:
            try:
                result = publish.publish_archive(Path("/safe/image.tar"), "v0.1.0-beta.1", execute)
            finally:
                self.last_calls = [call.args[0] for call in process.call_args_list]
            return result, self.last_calls

    def test_new_tag_copies_all_without_rebuild_and_verifies_digest(self):
        result, calls = self.call([(0, b"root", b""), (0, b'{"Tags":[]}', b""), (0, b"", b""), (0, b"root", b"")])
        self.assertEqual(result["state"], "published")
        self.assertEqual(calls[2], ["skopeo", "copy", "--all", "--preserve-digests", "oci-archive:/safe/image.tar", "docker://" + publish.IMAGE + ":v0.1.0-beta.1"])

    def test_matching_tag_retry_is_read_only(self):
        result, calls = self.call([(0, b"root", b""), (0, b'{"Tags":["v0.1.0-beta.1"]}', b""), (0, b"root", b"")])
        self.assertEqual(result["state"], "already-published")
        self.assertFalse(any("copy" in call for call in calls))

    def test_conflicting_readable_tag_never_copies(self):
        with self.assertRaises(rc.ReleaseError):
            self.call([(0, b"root", b""), (0, b'{"Tags":["v0.1.0-beta.1"]}', b""), (0, b"different", b"")])
        self.assertFalse(any("copy" in call for call in self.last_calls))

    def test_postcopy_digest_must_still_match(self):
        with self.assertRaises(rc.ReleaseError):
            self.call([(0, b"root", b""), (0, b'{"Tags":[]}', b""), (0, b"", b""), (0, b"changed", b"")])

    def test_dry_run_never_writes_and_verified_new_repository_is_supported(self):
        result, calls = self.call([(0, b"root", b""), (1, b"", b"NAME_UNKNOWN")], execute=False)
        self.assertEqual(result["state"], "verified-only")
        self.assertFalse(any("copy" in call for call in calls))

    def test_first_namespace_bootstrap_uses_only_digest_before_readable_inventory(self):
        for error in (b"Requesting bearer token: received unexpected HTTP status: 403 Forbidden", b"name unknown: repository name not known to registry", b"StatusCode: 403", b"unauthorized", b"unrecognized registry response"):
            outputs = [(0, b"root", b""), (1, b"", error), (0, b"", b""), (0, b"root", b""), (0, b'{"Tags":null}', b""), (0, b"", b""), (0, b"root", b"")]
            result, calls = self.call(outputs)
            self.assertEqual(result["state"], "published")
            self.assertEqual(calls[2], ["skopeo", "copy", "--all", "--preserve-digests", "oci-archive:/safe/image.tar", "docker://" + publish.IMAGE + "@sha256:" + rc.digest(b"root")])
            self.assertEqual(calls[4], ["skopeo", "list-tags", "docker://" + publish.IMAGE])
            self.assertEqual(calls[5][-1], "docker://" + publish.IMAGE + ":v0.1.0-beta.1")

    def test_bootstrap_cannot_bypass_unreadable_inventory_or_conflicting_tag(self):
        for error in (b"403 Forbidden", b"unauthorized", b"unknown response"):
            prefix = [(0, b"root", b""), (1, b"", error), (0, b"", b""), (0, b"root", b"")]
            for tail in ([(1, b"", error)], [(0, b'{"Tags":["v0.1.0-beta.1"]}', b""), (0, b"conflict", b"")]):
                with self.assertRaises(publish.RegistryError):
                    self.call(prefix + tail)
                copies = [c for c in self.last_calls if "copy" in c]
                self.assertEqual(len(copies), 1)
                self.assertIn("@sha256:", copies[0][-1])

    def test_failed_digest_bootstrap_never_inspects_or_writes_version_tags(self):
        for error in (b"unauthorized", b"TLS handshake timeout", b"unknown registry error"):
            with self.assertRaisesRegex(publish.RegistryError, "immutable namespace bootstrap"):
                self.call([(0, b"root", b""), (1, b"", error), (1, b"", error)])
            self.assertEqual(len(self.last_calls), 3)
            self.assertIn("@sha256:", self.last_calls[-1][-1])

    def test_bootstrap_wrong_digest_never_reaches_named_tag(self):
        with self.assertRaisesRegex(publish.RegistryError, "bootstrap digest"):
            self.call([(0, b"root", b""), (1, b"", b"403 Forbidden"), (0, b"", b""), (0, b"changed", b"")])
        self.assertEqual(len([c for c in self.last_calls if "copy" in c]), 1)

    def test_403_dry_run_and_other_registry_errors_never_write(self):
        for error in (b"403 Forbidden", b"unauthorized", b"TLS handshake timeout", b"unknown registry error"):
            with self.assertRaises(publish.RegistryError):
                self.call([(0, b"root", b""), (1, b"", error)], execute=False)
            self.assertFalse(any("copy" in c for c in self.last_calls))

    def test_registry_diagnostic_reports_operation_without_raw_stderr(self):
        with self.assertRaisesRegex(publish.RegistryError, "immutable namespace bootstrap") as caught:
            self.call([(0, b"root", b""), (1, b"", b"403 Forbidden"), (1, b"", b"bearer-secret and signed-url")])
        self.assertNotIn("bearer-secret", str(caught.exception))
        self.assertNotIn("signed-url", str(caught.exception))

    def test_registry_categories_are_allowlisted_and_never_copy_error_text(self):
        for raw, expected in ((b"NAME_UNKNOWN bearer-secret", "not-found"), (b"StatusCode: 403 signed-url", "access-denied"), (b"429 Too Many Requests signed-url", "rate-limited"), (b"TLS handshake timeout bearer-secret", "transport"), (b"bearer-secret signed-url", "unclassified")):
            self.assertEqual(publish.registry_error_category(raw), expected)
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(publish.RegistryError):
            self.call([(0, b"root", b""), (1, b"", b"bearer-secret signed-url"), (1, b"", b"bearer-secret signed-url")])
        self.assertIn("unclassified", output.getvalue())
        self.assertNotIn("bearer-secret", output.getvalue())
        self.assertNotIn("signed-url", output.getvalue())

    def test_verification_error_reports_phase_without_network_error_text(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["publish_ghcr.py", "--tag", "v0.1.0-beta.1"]), patch.object(publish, "verify_release", side_effect=rc.GitHubError("private-signed-url")), redirect_stderr(output):
            self.assertEqual(publish.main(), 1)
        self.assertIn("release provenance and payload verification", output.getvalue())
        self.assertNotIn("private-signed-url", output.getvalue())


if __name__ == "__main__":
    unittest.main()
