"""High-level Android OTA pins official release identity and never replays install."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


ott = load("ott", "cli/ott.py")
ota = load("ota", "cli/app_update.py")
SHA = "a" * 64
SOURCE = "b" * 40
TAG = "v1.1.58-beta.3"
APK = ota.VARIANTS["api22"]
PREFIX = "https://github.com/" + ota.REPOSITORY + "/releases/download/" + TAG + "/"
TARGET = dict(tag=TAG, variant="api22", version_code=10438, source_sha=SOURCE, sha256=SHA, url=PREFIX + APK)


def state(phase="idle", code=10427, sha="", **kwargs):
    return dict(version=1, phase=phase, sha256=sha, installed_code=code, installed_version="1.1.55",
                target_code=10438 if sha else 0, target_version="1.1.58-beta.3" if sha else "",
                error="", can_request_installs=True, user_confirmation_required=True, **kwargs)


def metadata(tag=TAG):
    prefix = "https://github.com/" + ota.REPOSITORY + "/releases/download/" + tag + "/"
    assets = [{"name": name, "size": size, "browser_download_url": prefix + name}
              for name, size in (("release-manifest.json", 1000), (APK, 12000000))]
    release = dict(tag_name=tag, draft=False, prerelease=True, published_at="2026-10-19T22:54:08Z", assets=assets)
    manifest = dict(schema=1, repository=ota.REPOSITORY, source_sha=SOURCE, tag=tag, channel="beta",
                    version_plan=dict(schema_version=1, source_sha=SOURCE, tag=tag, channel="beta",
                                      version=tag[1:], build_number=10438),
                    assets=[dict(name=APK, sha256=SHA, size=12000000)],
                    package_versions=[dict(path=APK, field="version", version=tag[1:]),
                                      dict(path=APK, field="versionCode", version="10438")])
    return release, manifest


def promotion_metadata():
    """Stable publication retains the RC manifest and final package version."""
    tag, candidate_tag = "v1.1.60", "v1.1.60-rc.1"
    stable, _ = metadata(tag)
    candidate, manifest = metadata(candidate_tag)
    stable["prerelease"] = False
    policy = {"versioning": {"promotion": "promote-bytes"}}
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    plan = dict(schema_version=1, base_version="1.1.60", version="1.1.60-rc.1", channel="rc",
                sequence=1, tag=candidate_tag, source_sha=SOURCE, build_number=10497,
                promotion="promote-bytes", policy_sha256=digest(policy))
    manifest.update(channel="rc", version="1.1.60", version_plan=plan,
                    source_policy={"data": policy}, plan_sha256=digest(plan))
    manifest["package_versions"] = [
        dict(path=APK, field="version", version="1.1.60", plan_sha256=digest(plan)),
        dict(path=APK, field="versionCode", version="10497", plan_sha256=digest(plan)),
    ]
    for release in (stable, candidate):
        release["assets"][0]["digest"] = "sha256:" + digest(manifest)
        release["assets"][1]["digest"] = "sha256:" + SHA
    return dict(stable=stable, candidate=candidate, manifest=manifest,
                candidate_manifest=copy.deepcopy(manifest), stable_source=SOURCE, candidate_source=SOURCE)


class Client:
    timeout = 45

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def call(self, device, action, params):
        self.calls.append((device, action, params))
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ReleaseTests(unittest.TestCase):
    def opts(self, *tail):
        return ota.options(["update", "--variant", "api22", *tail], ott.Error)

    def resolve(self, mutate=None, explicit=False):
        release, manifest = metadata()
        if mutate:
            mutate(release, manifest)
        def fetch(url, error, limit=None, deadline=None):
            if url.endswith("/releases?per_page=100"):
                return [dict(release, draft=True, tag_name="v99.0.0-beta.1"), release]
            if "/releases/tags/" in url:
                return release
            if url.endswith("release-manifest.json"):
                return manifest
            if "/git/ref/tags/" in url:
                return {"object": {"type": "commit", "sha": SOURCE}}
            self.fail("Unexpected public metadata request: " + url)
        with patch.object(ota, "request_json", side_effect=fetch):
            return ota.release_target(self.opts(*(["--version", TAG] if explicit else [])), ott.Error)

    def test_default_beta_and_explicit_tag(self):
        self.assertEqual(self.resolve(), TARGET)
        self.assertEqual(self.resolve(explicit=True), TARGET)
        self.assertTrue(ota.high_level(["update"]))
        self.assertFalse(ota.high_level(["update", "status"]))

    def test_options_never_guess_variant_or_accept_urls(self):
        for words in (["update"], ["update", "--variant", "unknown"],
                      ["update", "--variant", "api22", "--version", "https://evil/a"],
                      ["update", "--variant", "api22", "--wait", "nan"]):
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ota.options(words, ott.Error)
        self.assertEqual(self.opts("--version", "1.1.58")["version"], "v1.1.58")

    def test_explicit_beta_selector_ignores_case(self):
        self.assertEqual(ota.options(["UPDATE", "BeTa", "--variant", "api22"], ott.Error), self.opts())

    def test_latest_beta_is_numeric_not_the_first_release_in_the_api_page(self):
        tag = "v1.1.58-beta.11"
        release, manifest = metadata(tag)
        releases = [metadata("v1.1.58-beta.9")[0], release, metadata("v1.1.58-beta.8")[0],
                    dict(release, tag_name="v99.0.0-beta.1", draft=True),
                    dict(release, tag_name="v99.0.0", prerelease=False)]
        expected_urls = [
            ota.API + "/releases?per_page=100",
            "https://github.com/" + ota.REPOSITORY + "/releases/download/" + tag + "/release-manifest.json",
            ota.API + "/git/ref/tags/" + tag,
        ]
        with patch.object(ota, "request_json", side_effect=[
            releases, manifest, {"object": {"type": "commit", "sha": SOURCE}},
        ]) as fetch:
            selected = ota.release_target(self.opts(), ott.Error)
        self.assertEqual(selected["tag"], tag)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], expected_urls)

    def test_rejects_unpublished_mismatched_or_ambiguous_metadata(self):
        mutations = [lambda r, m: r.update(draft=True),
                     lambda r, m: m.update(source_sha="c" * 40),
                     lambda r, m: m.update(repository="other/repository"),
                     lambda r, m: m.update(schema=True),
                     lambda r, m: m["version_plan"].update(build_number=True),
                     lambda r, m: m["version_plan"].update(channel="stable"),
                     lambda r, m: r["assets"][1].update(browser_download_url="https://evil/app.apk"),
                     lambda r, m: m["assets"].append(copy.deepcopy(m["assets"][0])),
                     lambda r, m: m["assets"][0].update(sha256="BAD"),
                     lambda r, m: m["package_versions"][1].update(version="10439")]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(ott.Error):
                self.resolve(mutate, explicit=True)

    def resolve_promotion(self, mutate=None, *, annotated=()):
        proof = promotion_metadata()
        if mutate:
            mutate(proof)
        stable_url = "https://github.com/" + ota.REPOSITORY + "/releases/download/v1.1.60/"
        candidate_url = "https://github.com/" + ota.REPOSITORY + "/releases/download/v1.1.60-rc.1/"
        replies = {
            ota.API + "/releases/tags/v1.1.60": proof["stable"],
            ota.API + "/releases/tags/v1.1.60-rc.1": proof["candidate"],
            stable_url + "release-manifest.json": proof["manifest"],
            candidate_url + "release-manifest.json": proof["candidate_manifest"],
            ota.API + "/git/ref/tags/v1.1.60": {"object": {"type": "commit", "sha": proof["stable_source"]}},
            ota.API + "/git/ref/tags/v1.1.60-rc.1": {"object": {"type": "commit", "sha": proof["candidate_source"]}},
        }
        for name in annotated:
            tag, tag_sha = {"stable": ("v1.1.60", "d" * 40),
                            "candidate": ("v1.1.60-rc.1", "e" * 40)}[name]
            replies[ota.API + "/git/ref/tags/" + tag] = {"object": {"type": "tag", "sha": tag_sha}}
            replies[ota.API + "/git/tags/" + tag_sha] = {
                "object": {"type": "commit", "sha": proof[name + "_source"]}}
        def fetch(url, error, limit=None, deadline=None):
            self.assertEqual(deadline, 123)
            self.assertIn(url, replies)
            return replies[url]
        with patch.object(ota, "request_json", side_effect=fetch) as request:
            target = ota.release_target(self.opts("--version", "v1.1.60"), ott.Error, deadline=123)
        self.assertEqual(request.call_count, 6 + len(annotated))
        return target

    def test_stable_byte_promotion_retains_rc_evidence_and_final_package_version(self):
        target = self.resolve_promotion()
        self.assertEqual(target, dict(tag="v1.1.60", variant="api22", version_code=10497,
                                      source_sha=SOURCE, sha256=SHA,
                                      url="https://github.com/" + ota.REPOSITORY +
                                          "/releases/download/v1.1.60/" + APK))

    def test_stable_promotion_dereferences_annotated_tags_with_the_same_deadline(self):
        for annotated in (("stable",), ("candidate",), ("stable", "candidate")):
            with self.subTest(annotated=annotated):
                target = self.resolve_promotion(annotated=annotated)
                self.assertEqual(target["source_sha"], SOURCE)
                self.assertEqual(target["tag"], "v1.1.60")

    def test_stable_promotion_rejects_a_different_annotated_commit(self):
        for name in ("stable", "candidate"):
            def mutate(proof):
                proof[name + "_source"] = "c" * 40
            with self.subTest(name=name), self.assertRaises(ott.Error):
                self.resolve_promotion(mutate, annotated=("stable", "candidate"))

    def test_stable_promotion_rejects_mismatched_source_plan_or_package(self):
        mutations = {
            "stable tag source": lambda p: p.update(stable_source="c" * 40),
            "candidate tag source": lambda p: p.update(candidate_source="c" * 40),
            "manifest source": lambda p: p["manifest"].update(source_sha="c" * 40),
            "plan source": lambda p: p["manifest"]["version_plan"].update(source_sha="c" * 40),
            "plan digest": lambda p: p["manifest"].update(plan_sha256="c" * 64),
            "policy digest": lambda p: p["manifest"]["version_plan"].update(policy_sha256="c" * 64),
            "policy profile": lambda p: p["manifest"]["source_policy"]["data"]["versioning"].update(promotion="final-build"),
            "plan profile": lambda p: p["manifest"]["version_plan"].update(promotion="final-build"),
            "base version": lambda p: p["manifest"]["version_plan"].update(base_version="1.1.61"),
            "manifest version": lambda p: p["manifest"].update(version="1.1.61"),
            "candidate identity": lambda p: p["manifest"].update(tag="v1.1.61-rc.1"),
            "beta promotion": lambda p: p["manifest"].update(tag="v1.1.60-beta.1", channel="beta"),
            "sequence type": lambda p: p["manifest"]["version_plan"].update(sequence=True),
            "sequence mismatch": lambda p: p["manifest"]["version_plan"].update(sequence=2),
            "extra plan field": lambda p: p["manifest"]["version_plan"].update(unrecognized=True),
            "wrong package version": lambda p: p["manifest"]["package_versions"][0].update(version="1.1.60-rc.1"),
            "wrong package plan": lambda p: p["manifest"]["package_versions"][1].update(plan_sha256="c" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), self.assertRaises(ott.Error):
                self.resolve_promotion(mutate)

    def test_stable_promotion_requires_matching_published_rc_manifest_and_apk(self):
        mutations = {
            "stable prerelease": lambda p: p["stable"].update(prerelease=True),
            "candidate draft": lambda p: p["candidate"].update(draft=True),
            "candidate stable": lambda p: p["candidate"].update(prerelease=False),
            "candidate unpublished": lambda p: p["candidate"].update(published_at=None),
            "candidate manifest digest": lambda p: p["candidate"]["assets"][0].update(digest="sha256:" + "c" * 64),
            "candidate manifest contents": lambda p: p["candidate_manifest"].update(run_id=999),
            "candidate manifest type": lambda p: p["candidate_manifest"].update(schema=True),
            "candidate manifest size": lambda p: p["candidate"]["assets"][0].update(size=1001),
            "missing manifest digest": lambda p: p["stable"]["assets"][0].pop("digest"),
            "stable apk digest": lambda p: p["stable"]["assets"][1].update(digest="sha256:" + "c" * 64),
            "candidate apk digest": lambda p: p["candidate"]["assets"][1].update(digest="sha256:" + "c" * 64),
            "candidate apk size": lambda p: p["candidate"]["assets"][1].update(size=12000001),
            "candidate apk path": lambda p: p["candidate"]["assets"][1].update(browser_download_url="https://example.test/app.apk"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), self.assertRaises(ott.Error):
                self.resolve_promotion(mutate)

    def test_promoted_package_rows_still_require_the_final_version_and_same_plan(self):
        for row, key, value in ((0, "version", "1.1.60-rc.1"),
                                (1, "version", "10498"),
                                (0, "plan_sha256", "c" * 64)):
            def mutate(proof):
                for name in ("manifest", "candidate_manifest"):
                    proof[name]["package_versions"][row][key] = value
            with self.subTest(row=row, key=key), self.assertRaisesRegex(ott.Error, "APK version metadata"):
                self.resolve_promotion(mutate)

    def test_direct_stable_release_still_uses_its_own_plan(self):
        tag = "v1.1.60"
        release, manifest = metadata(tag)
        release["prerelease"] = False
        manifest["channel"] = manifest["version_plan"]["channel"] = "stable"
        with patch.object(ota, "request_json", side_effect=[
            release, manifest, {"object": {"type": "commit", "sha": SOURCE}},
        ]) as fetch:
            selected = ota.release_target(self.opts("--version", tag), ott.Error)
        self.assertEqual(selected["tag"], tag)
        self.assertEqual(fetch.call_count, 3)

    def workflow(self, replies, *tail):
        client = Client(replies)
        with patch.object(ota, "release_target", return_value=TARGET), patch.object(ota.time, "sleep"):
            result = ota.update(client, "device", self.opts(*tail), ott)
        self.assertEqual(client.timeout, 45)
        return result, client

    def test_prepare_poll_install_and_verify(self):
        result, client = self.workflow([state(), state("downloading", sha=SHA), state("ready", sha=SHA),
                                       {"accepted": True, "operation": "install"}, state("installed", code=10438, sha=SHA)])
        self.assertEqual([c[2]["operation"] for c in client.calls], ["status", "prepare", "status", "install", "status"])
        self.assertEqual(result["phase"], "installed")
        self.assertNotIn("url", result["release"])

    def test_prepare_only_and_confirmation_are_honest(self):
        result, client = self.workflow([state(), state("ready", sha=SHA)], "--prepare-only")
        self.assertEqual(result["phase"], "ready")
        self.assertNotIn("install", [c[2]["operation"] for c in client.calls])
        result, client = self.workflow([state("ready", sha=SHA), {"accepted": True, "operation": "install"},
                                       state("awaiting_confirmation", sha=SHA)])
        self.assertEqual(result["phase"], "awaiting_confirmation")
        self.assertEqual(sum(c[2]["operation"] == "install" for c in client.calls), 1)
        for phase in ("idle", "installed"):
            with self.subTest(phase=phase), self.assertRaises(ott.Error):
                self.workflow([state(), state(phase, sha=SHA)], "--prepare-only")

    def test_pending_install_never_replayed(self):
        result, client = self.workflow([state("awaiting_confirmation", sha=SHA)])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result["phase"], "awaiting_confirmation")
        result, client = self.workflow([state("ready", sha=SHA), {"accepted": True, "operation": "install"}, ott.Error("offline")])
        self.assertIn("not yet verified", result["message"])
        self.assertEqual(sum(c[2]["operation"] == "install" for c in client.calls), 1)

    def test_correlates_staged_version_and_distinguishes_superseded_install(self):
        for key, value in (("target_code", 10439), ("target_version", "other")):
            staged = state("ready", sha=SHA)
            staged[key] = value
            with self.subTest(key=key), self.assertRaises(ott.Error):
                self.workflow([staged])
        result, _ = self.workflow([state("ready", sha=SHA), {"accepted": True, "operation": "install"},
                                   state("installed", code=10439, sha=SHA)])
        self.assertIn("superseded", result["message"])

    def test_public_metadata_obeys_overall_deadline_even_if_read_stalls(self):
        import queue
        import threading
        read_started, release_read, read_finished = (threading.Event() for _ in range(3))
        response_closed = threading.Event()
        workers, open_budgets, wait_budgets = [], [], []
        case = self

        class FinishedQueue(queue.Queue):
            def get(self, block=True, timeout=None):
                wait_budgets.append(timeout)
                case.assertAlmostEqual(timeout, 0.02)
                case.assertTrue(read_started.wait(5), "metadata worker did not enter its read")
                return super().get(block=block, timeout=timeout)

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                response_closed.set()
            def read1(self, size):
                read_started.set()
                if not release_read.wait(5):
                    raise RuntimeError("test did not release its metadata reader")
                read_finished.set()
                return b"{}"

        def open_response(*args, **kwargs):
            open_budgets.append(kwargs["timeout"])
            return Response()

        def thread(*args, **kwargs):
            worker = threading.Thread(*args, **kwargs)
            workers.append(worker)
            return worker

        # The clock fixes the budget; events establish ordering independently of
        # runner scheduling. The real Queue still expires while read1 is blocked.
        with patch.object(ota.urllib.request, "build_opener", return_value=SimpleNamespace(open=open_response)), \
                patch.object(ota, "time", SimpleNamespace(monotonic=lambda: 100.0)), \
                patch.object(ota, "queue", SimpleNamespace(Queue=FinishedQueue, Empty=queue.Empty)), \
                patch.object(ota, "threading", SimpleNamespace(Thread=thread, Event=threading.Event)):
            try:
                with self.assertRaisesRegex(ott.Error, "Official release metadata timed out"):
                    ota.request_json(ota.API, ott.Error, deadline=100.02)
                self.assertTrue(read_started.is_set())
                self.assertFalse(read_finished.is_set(), "caller waited for the blocked read")
                self.assertEqual(len(workers), 1)
                self.assertTrue(workers[0].is_alive())
            finally:
                release_read.set()
                for worker in workers:
                    worker.join(5)
                    self.assertFalse(worker.is_alive(), "metadata worker leaked after cleanup")
        self.assertTrue(read_finished.is_set())
        self.assertTrue(response_closed.is_set())
        self.assertEqual(len(open_budgets), 1)
        self.assertEqual(len(wait_budgets), 1)
        self.assertAlmostEqual(open_budgets[0], 0.02)

    def test_permission_continuation_and_replacement_rejected(self):
        blocked = state("awaiting_permission", sha=SHA)
        blocked["can_request_installs"] = False
        result, client = self.workflow([blocked])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result["phase"], "awaiting_permission")
        with self.assertRaises(ott.Error):
            self.workflow([state("downloading", sha="c" * 64)])
        with self.assertRaises(ott.Error):
            self.workflow([state(), state("downloading", sha=SHA), state("ready", sha="c" * 64)])

    def test_equal_and_older_targets_never_mutate(self):
        result, client = self.workflow([state(code=10438)])
        self.assertEqual(len(client.calls), 1)
        self.assertIn("already installed", result["message"])
        result, client = self.workflow([state(code=10439)])
        self.assertEqual(len(client.calls), 1)
        with self.assertRaisesRegex(ott.Error, "downgrades"):
            self.workflow([state(code=10439)], "--version", TAG)

    def test_kiosk_rejection_does_not_send_install(self):
        client = Client([state(), ott.Error("Unlock settings and disable kiosk before updating")])
        with patch.object(ota, "release_target", return_value=TARGET), self.assertRaises(ott.Error):
            ota.update(client, "device", self.opts(), ott)
        self.assertEqual([c[2]["operation"] for c in client.calls], ["status", "prepare"])

    def test_main_routes_bare_update_to_orchestrator(self):
        client = SimpleNamespace(timeout=45, device=lambda value: "device")
        invoked = []
        module = SimpleNamespace(run=lambda *args: invoked.append(args) or 0)
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: None))
        with patch.object(ott, "read_json", return_value={}), patch.object(ott, "Client", return_value=client), \
                patch.object(ott, "management", return_value=False), \
                patch.object(ott.importlib.util, "spec_from_file_location", return_value=spec), \
                patch.object(ott.importlib.util, "module_from_spec", return_value=module):
            self.assertEqual(ott.main(["k8a", "update"]), 0)
        self.assertEqual(invoked[0][3], ["update"])


if __name__ == "__main__":
    unittest.main()
