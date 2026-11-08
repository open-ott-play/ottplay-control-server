"""High-level Android OTA pins official release identity and never replays install."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
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


def bind_candidate_bytes(proof):
    # The published digest covers bytes, including formatting and trailing newline.
    raw = (json.dumps(proof['candidate_manifest'], indent=3, ensure_ascii=False) + '\n').encode()
    proof['candidate_raw'] = raw
    digest = hashlib.sha256(raw).hexdigest()
    proof['manifest']['rc_evidence']['manifest_sha256'] = digest
    proof['candidate']['assets'][0].update(size=len(raw), digest='sha256:' + digest)


def compact_metadata():
    proof = promotion_metadata()
    candidate = proof['candidate_manifest']
    candidate.update(run_id=123456, run_attempt=2)
    full = dict(name=ota.VARIANTS['full'], size=13000000, sha256='c' * 64)
    candidate['assets'].append(full)
    for row in copy.deepcopy(candidate['package_versions']):
        row['path'] = full['name']
        candidate['package_versions'].append(row)
    for release in (proof['stable'], proof['candidate']):
        prefix = 'https://github.com/' + ota.REPOSITORY + '/releases/download/' + release['tag_name'] + '/'
        release['assets'].append(dict(name=full['name'], size=full['size'],
                                      digest='sha256:' + full['sha256'], browser_download_url=prefix + full['name']))
    proof['manifest'] = dict(schema=2, kind='rc-payloads-v1', repository=ota.REPOSITORY,
                             version='1.1.60', channel='stable', tag='v1.1.60', source_sha=SOURCE,
                             assets=copy.deepcopy(candidate['assets']),
                             rc_evidence=dict(tag='v1.1.60-rc.1', manifest_sha256='', source_sha=SOURCE,
                                              run_id=123456, run_attempt=2))
    receipt = dict(name='release-inputs-android.json', size=1000, sha256='e' * 64)
    candidate['assets'].append(receipt)
    candidate['build_receipts'] = [{key: receipt[key] for key in ('name', 'sha256')}]
    proof['candidate']['assets'].append(dict(name=receipt['name'], size=receipt['size'],
        digest='sha256:' + receipt['sha256'], browser_download_url='https://github.com/' + ota.REPOSITORY +
        '/releases/download/v1.1.60-rc.1/' + receipt['name']))
    bind_candidate_bytes(proof)
    raw = (json.dumps(proof['manifest'], indent=2) + '\n').encode()
    proof['stable']['assets'][0].update(size=len(raw), digest='sha256:' + hashlib.sha256(raw).hexdigest())
    return proof


def add_compact_asset(proof, name, *, shipped=True):
    row = dict(name=name, size=1000, sha256='f' * 64)
    proof['candidate_manifest']['assets'].append(copy.deepcopy(row))
    releases = [proof['candidate']]
    if shipped:
        proof['manifest']['assets'].append(copy.deepcopy(row))
        proof['manifest']['assets'].sort(key=lambda value: value['name'])
        releases.append(proof['stable'])
    for release in releases:
        release['assets'].append(dict(name=name, size=row['size'], digest='sha256:' + row['sha256'],
            browser_download_url='https://github.com/' + ota.REPOSITORY +
            '/releases/download/' + release['tag_name'] + '/' + name))
    return row


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


class CompactReleaseTests(unittest.TestCase):
    def opts(self, variant='api22', version='v1.1.60'):
        return ota.options(['update', '--variant', variant, '--version', version], ott.Error)

    def fetcher(self, proof, *, deadline=123, cancelled=None):
        stable_tag = proof['stable']['tag_name']
        candidate_tag = proof['manifest']['rc_evidence']['tag']
        stable_url = 'https://github.com/' + ota.REPOSITORY + '/releases/download/' + stable_tag + '/'
        candidate_url = 'https://github.com/' + ota.REPOSITORY + '/releases/download/' + candidate_tag + '/'
        rc_manifest_url = candidate_url + 'release-manifest.json'
        replies = {
            ota.API + '/releases/tags/' + stable_tag: proof['stable'],
            ota.API + '/releases/tags/' + candidate_tag: proof['candidate'],
            stable_url + 'release-manifest.json': proof['manifest'],
            rc_manifest_url: proof['candidate_manifest'],
            ota.API + '/git/ref/tags/' + stable_tag: {'object': {'type': 'commit', 'sha': proof['stable_source']}},
            ota.API + '/git/ref/tags/' + candidate_tag: {'object': {'type': 'commit', 'sha': proof['candidate_source']}},
        }
        expected_deadline = deadline
        def fetch(url, error, limit=None, deadline=None, *, group_cancelled=None, expected_sha256=None):
            self.assertEqual(deadline, expected_deadline)
            self.assertIs(group_cancelled, cancelled)
            self.assertIn(url, replies, 'Unexpected release metadata URL')
            if url == rc_manifest_url:
                self.assertEqual(expected_sha256, proof['manifest']['rc_evidence']['manifest_sha256'])
                if expected_sha256 != hashlib.sha256(proof['candidate_raw']).hexdigest():
                    raise error('Cannot read verified official release metadata; no update was requested')
            else:
                self.assertIsNone(expected_sha256)
            return replies[url]
        return fetch

    def test_compact_stable_resolves_both_variants_without_recursing_into_rc_release(self):
        for variant in ota.VARIANTS:
            with self.subTest(variant=variant):
                proof = compact_metadata()
                cancelled = threading.Event()
                with patch.object(ota, 'request_json', side_effect=self.fetcher(proof, cancelled=cancelled)) as fetch, \
                        patch.object(ota, 'release_target', wraps=ota.release_target) as resolve:
                    target = ota.release_target(self.opts(variant), ott.Error, deadline=123, group_cancelled=cancelled)
                resolve.assert_called_once()
                row = next(row for row in proof['manifest']['assets'] if row['name'] == ota.VARIANTS[variant])
                self.assertEqual(target, dict(tag='v1.1.60', variant=variant, version_code=10497,
                    source_sha=SOURCE, sha256=row['sha256'], url='https://github.com/' + ota.REPOSITORY +
                    '/releases/download/v1.1.60/' + row['name']))
                hashes = [call.kwargs['expected_sha256'] for call in fetch.call_args_list if 'expected_sha256' in call.kwargs]
                self.assertEqual(hashes, [proof['manifest']['rc_evidence']['manifest_sha256']])

    def assert_proof_rejected_before_update(self, proof):
        client = Client([state()])
        client.group_cancelled = threading.Event()
        opts = self.opts(version=proof['stable']['tag_name'])
        with patch.object(ota.time, 'monotonic', return_value=100), \
                patch.object(ota, 'request_json', side_effect=self.fetcher(
                    proof, deadline=100 + opts['wait'], cancelled=client.group_cancelled)), \
                self.assertRaises(ott.Error):
            ota.update(client, 'fixture-player', opts, ott)
        self.assertEqual(client.calls, [('fixture-player', 'app_update', {'operation': 'status'})])
        self.assertEqual(client.timeout, 45)

    def test_compact_identity_failures_never_prepare_or_install(self):
        changes = {
            'schema boolean': lambda p: p['manifest'].update(schema=True),
            'wrong kind': lambda p: p['manifest'].update(kind='other'),
            'extra top field': lambda p: p['manifest'].update(extra='not-allowed'),
            'repository': lambda p: p['manifest'].update(repository='other/repository'),
            'nonstable channel': lambda p: p['manifest'].update(channel='beta'),
            'wrong stable version': lambda p: p['manifest'].update(version='1.1.61'),
            'wrong stable tag': lambda p: p['manifest'].update(tag='v1.1.61'),
            'stable prerelease': lambda p: p['stable'].update(prerelease=True),
            'stable source': lambda p: p.update(stable_source='d' * 40),
            'outer source': lambda p: p['manifest'].update(source_sha='d' * 40),
            'RC tag source': lambda p: p.update(candidate_source='d' * 40),
            'evidence source': lambda p: p['manifest']['rc_evidence'].update(source_sha='d' * 40),
            'RC base mismatch': lambda p: p['manifest']['rc_evidence'].update(tag='v1.1.61-rc.1'),
            'RC beta tag': lambda p: p['manifest']['rc_evidence'].update(tag='v1.1.60-beta.1'),
            'RC hash': lambda p: p['manifest']['rc_evidence'].update(manifest_sha256='d' * 64),
            'RC hash syntax': lambda p: p['manifest']['rc_evidence'].update(manifest_sha256='BAD'),
            'run id mismatch': lambda p: p['manifest']['rc_evidence'].update(run_id=123457),
            'run attempt mismatch': lambda p: p['manifest']['rc_evidence'].update(run_attempt=3),
            'run id boolean': lambda p: p['manifest']['rc_evidence'].update(run_id=True),
            'attempt boolean': lambda p: p['manifest']['rc_evidence'].update(run_attempt=True),
            'zero run': lambda p: p['manifest']['rc_evidence'].update(run_id=0),
            'zero attempt': lambda p: p['manifest']['rc_evidence'].update(run_attempt=0),
            'extra evidence field': lambda p: p['manifest']['rc_evidence'].update(url='https://private.test/token'),
            'missing RC': lambda p: p.update(candidate=None),
            'RC draft': lambda p: p['candidate'].update(draft=True),
            'RC unpublished': lambda p: p['candidate'].update(published_at=None),
            'RC not prerelease': lambda p: p['candidate'].update(prerelease=False),
        }
        for name, mutate in changes.items():
            with self.subTest(name=name):
                proof = compact_metadata()
                mutate(proof)
                self.assert_proof_rejected_before_update(proof)

    def test_compact_asset_failures_never_prepare_or_install(self):
        changes = {
            'missing stable row': lambda p: p['manifest']['assets'].pop(0),
            'duplicate stable row': lambda p: p['manifest']['assets'].append(copy.deepcopy(p['manifest']['assets'][0])),
            'unexpected row key': lambda p: p['manifest']['assets'][0].update(url='https://private.test/token'),
            'stable row size': lambda p: p['manifest']['assets'][0].update(size=12000001),
            'stable row size boolean': lambda p: p['manifest']['assets'][0].update(size=True),
            'stable row hash': lambda p: p['manifest']['assets'][0].update(sha256='d' * 64),
            'missing stable APK': lambda p: p['stable']['assets'].pop(1),
            'duplicate stable APK': lambda p: p['stable']['assets'].append(copy.deepcopy(p['stable']['assets'][1])),
            'stable APK digest': lambda p: p['stable']['assets'][1].update(digest='sha256:' + 'd' * 64),
            'RC APK digest': lambda p: p['candidate']['assets'][1].update(digest='sha256:' + 'd' * 64),
            'RC APK size': lambda p: p['candidate']['assets'][1].update(size=12000001),
            'missing RC APK': lambda p: p['candidate']['assets'].pop(1),
            'duplicate RC APK': lambda p: p['candidate']['assets'].append(copy.deepcopy(p['candidate']['assets'][1])),
            'stable APK untrusted URL': lambda p: p['stable']['assets'][1].update(browser_download_url='https://private.test/app.apk'),
            'RC APK untrusted URL': lambda p: p['candidate']['assets'][1].update(browser_download_url='https://private.test/app.apk'),
            'RC manifest untrusted URL': lambda p: p['candidate']['assets'][0].update(browser_download_url='https://private.test/manifest.json'),
            'RC manifest digest': lambda p: p['candidate']['assets'][0].update(digest='sha256:' + 'd' * 64),
            'oversize stable manifest': lambda p: p['stable']['assets'][0].update(size=1024 * 1024 + 1),
            'oversize RC manifest': lambda p: p['candidate']['assets'][0].update(size=1024 * 1024 + 1),
            'oversize APK': lambda p: p['stable']['assets'][1].update(size=128 * 1024 * 1024 + 1),
        }
        for name, mutate in changes.items():
            with self.subTest(name=name):
                proof = compact_metadata()
                mutate(proof)
                self.assert_proof_rejected_before_update(proof)

    def test_compact_revalidates_rc_contents_after_raw_digest_matches(self):
        changes = {
            'manifest source': lambda m: m.update(source_sha='d' * 40),
            'manifest schema': lambda m: m.update(schema=2),
            'manifest tag': lambda m: m.update(tag='v1.1.60-rc.2'),
            'manifest channel': lambda m: m.update(channel='beta'),
            'plan source': lambda m: m['version_plan'].update(source_sha='d' * 40),
            'plan hash': lambda m: m.update(plan_sha256='d' * 64),
            'wrong package version': lambda m: m['package_versions'][0].update(version='1.1.60-rc.1'),
            'wrong versionCode': lambda m: m['package_versions'][1].update(version='10498'),
            'wrong package plan': lambda m: m['package_versions'][1].update(plan_sha256='d' * 64),
            'duplicate package field': lambda m: m['package_versions'].append(copy.deepcopy(m['package_versions'][0])),
            'missing package field': lambda m: m['package_versions'].pop(0),
            'duplicate RC asset': lambda m: m['assets'].append(copy.deepcopy(m['assets'][0])),
            'missing RC asset': lambda m: m['assets'].pop(0),
        }
        for name, mutate in changes.items():
            with self.subTest(name=name):
                proof = compact_metadata()
                mutate(proof['candidate_manifest'])
                bind_candidate_bytes(proof)
                self.assert_proof_rejected_before_update(proof)

    def test_compact_rejects_noncanonical_asset_names_and_casefold_collisions(self):
        names = ['.hidden', '-leading', 'contains space.json', 'nested/payload.json',
                 'nested\\payload.json', 'méta.json', 'a' * 201, 'RELEASE-MANIFEST.JSON']
        for name in names:
            with self.subTest(name=name):
                proof = compact_metadata()
                add_compact_asset(proof, name)
                bind_candidate_bytes(proof)
                self.assert_proof_rejected_before_update(proof)
        proof = compact_metadata()
        add_compact_asset(proof, 'latest.json')
        add_compact_asset(proof, 'LATEST.JSON')
        bind_candidate_bytes(proof)
        self.assert_proof_rejected_before_update(proof)

    def test_compact_rejects_leading_zero_versions_even_when_all_references_agree(self):
        def replace_version(value, version):
            if isinstance(value, dict):
                return {key: replace_version(item, version) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_version(item, version) for item in value]
            return value.replace('1.1.60', version) if isinstance(value, str) else value
        for version in ('01.1.60', '1.01.60', '1.1.060'):
            with self.subTest(version=version):
                proof = replace_version(compact_metadata(), version)
                candidate = proof['candidate_manifest']
                plan_hash = hashlib.sha256(json.dumps(candidate['version_plan'], sort_keys=True,
                                                     separators=(',', ':')).encode()).hexdigest()
                candidate['plan_sha256'] = plan_hash
                for row in candidate['package_versions']:
                    row['plan_sha256'] = plan_hash
                bind_candidate_bytes(proof)
                self.assert_proof_rejected_before_update(proof)

    def test_compact_fuzz_profile_must_be_a_nonempty_object(self):
        for profile in (True, 1, 'enabled', ['enabled'], {}):
            with self.subTest(profile=profile):
                proof = compact_metadata()
                receipt = add_compact_asset(proof, 'release-inputs-fuzz.json', shipped=False)
                proof['candidate_manifest']['validation_receipts'] = [
                    {key: receipt[key] for key in ('name', 'sha256')}]
                add_compact_asset(proof, 'fuzz-report.json', shipped=False)
                proof['candidate_manifest']['fuzzing_profile'] = profile
                bind_candidate_bytes(proof)
                self.assert_proof_rejected_before_update(proof)

    def test_compact_projection_omits_only_declared_receipts_and_exact_fuzz_report(self):
        proof = compact_metadata()
        proof['candidate_manifest']['assets'][0]['build_metadata'] = {'fixture': 'retained RC evidence'}
        receipt = add_compact_asset(proof, 'release-inputs-fuzz.json', shipped=False)
        proof['candidate_manifest']['validation_receipts'] = [{key: receipt[key] for key in ('name', 'sha256')}]
        proof['candidate_manifest']['fuzzing_profile'] = {'fixture': True}
        add_compact_asset(proof, 'fuzz-report.json', shipped=False)
        for name in ('latest.json', 'latest.json.sig', 'build-metadata.json',
                     'release-inputs-ui.png', 'release-inputs-android.json.sig', 'fuzz-report-extra.json',
                     'A+v1.2-foo_bar.bin', 'a' * 200):
            add_compact_asset(proof, name)
        bind_candidate_bytes(proof)
        with patch.object(ota, 'request_json', side_effect=self.fetcher(proof)):
            target = ota.release_target(self.opts(), ott.Error, deadline=123)
        self.assertEqual(target['tag'], 'v1.1.60')

    def test_compact_projection_rejects_missing_payloads_and_unproven_omissions(self):
        def declaration(proof, **values):
            proof['candidate_manifest']['build_receipts'][0].update(values)
        def fuzz_receipt(proof):
            row = add_compact_asset(proof, 'release-inputs-fuzz.json', shipped=False)
            proof['candidate_manifest']['validation_receipts'] = [{key: row[key] for key in ('name', 'sha256')}]
        def fuzz_without_profile(proof):
            fuzz_receipt(proof)
            add_compact_asset(proof, 'fuzz-report.json', shipped=False)
        changes = {
            'missing build declarations': lambda p: p['candidate_manifest'].pop('build_receipts'),
            'empty build declarations': lambda p: p['candidate_manifest'].update(build_receipts=[]),
            'invalid declaration collection': lambda p: p['candidate_manifest'].update(validation_receipts={}),
            'wrong receipt digest': lambda p: declaration(p, sha256='d' * 64),
            'missing declared receipt': lambda p: declaration(p, name='release-inputs-missing.json'),
            'untrusted receipt name': lambda p: declaration(p, name='build-metadata.json'),
            'duplicate receipt declaration': lambda p: p['candidate_manifest'].update(
                validation_receipts=copy.deepcopy(p['candidate_manifest']['build_receipts'])),
            'undeclared receipt': lambda p: add_compact_asset(p, 'release-inputs-extra.json', shipped=False),
            'undeclared fuzz report': lambda p: add_compact_asset(p, 'fuzz-report.json', shipped=False),
            'fuzz receipt without report': fuzz_receipt,
            'fuzz report without profile': fuzz_without_profile,
            'missing latest payload': lambda p: add_compact_asset(p, 'latest.json', shipped=False),
            'missing signature payload': lambda p: add_compact_asset(p, 'latest.json.sig', shipped=False),
            'prefix lookalike omission': lambda p: add_compact_asset(p, 'release-inputs-android.json.sig', shipped=False),
            'fuzz lookalike omission': lambda p: add_compact_asset(p, 'fuzz-report-extra.json', shipped=False),
            'unsorted stable payloads': lambda p: p['manifest']['assets'].reverse(),
            'invented stable payload': lambda p: p['manifest']['assets'].append(
                dict(name='not-in-rc.json', size=1000, sha256='f' * 64)),
        }
        for name, mutate in changes.items():
            with self.subTest(name=name):
                proof = compact_metadata()
                mutate(proof)
                bind_candidate_bytes(proof)
                self.assert_proof_rejected_before_update(proof)

    def test_request_json_hashes_downloaded_bytes_not_reserialized_json(self):
        values = [b'{"answer":42,"name":"fixture"}', b'{\n  "name": "fixture",\n  "answer": 42\n}\n']
        for raw in values:
            with self.subTest(raw=raw):
                opener = SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(raw))
                with patch.object(ota.urllib.request, 'build_opener', return_value=opener):
                    value = ota.request_json(ota.API, ott.Error, expected_sha256=hashlib.sha256(raw).hexdigest())
                self.assertEqual(value, {'answer': 42, 'name': 'fixture'})
        opener = SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(values[1]))
        with patch.object(ota.urllib.request, 'build_opener', return_value=opener), self.assertRaises(ott.Error):
            ota.request_json(ota.API, ott.Error, expected_sha256=hashlib.sha256(values[0]).hexdigest())

    def test_verified_digest_does_not_bypass_json_or_size_validation(self):
        for raw, limit in ((b'{"key":1,"key":2}', 1024), (b'{not-json}', 1024), (b'{"a":1}', 6)):
            with self.subTest(raw=raw):
                opener = SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(raw))
                with patch.object(ota.urllib.request, 'build_opener', return_value=opener), self.assertRaises(ott.Error):
                    ota.request_json(ota.API, ott.Error, limit=limit, expected_sha256=hashlib.sha256(raw).hexdigest())

    def test_hash_pinned_json_rejects_nonfinite_unknown_metadata(self):
        for constant in (b'NaN', b'Infinity', b'-Infinity'):
            raw = b'{"schema":1,"unknown_metadata":{"nested":[' + constant + b']}}'
            with self.subTest(constant=constant):
                opener = SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(raw))
                with patch.object(ota.urllib.request, 'build_opener', return_value=opener), self.assertRaises(ott.Error):
                    ota.request_json(ota.API, ott.Error, expected_sha256=hashlib.sha256(raw).hexdigest())

    def test_raw_manifest_download_honors_cancel_before_opening(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(ota.urllib.request, 'build_opener') as opener, self.assertRaises(KeyboardInterrupt):
            ota.request_json(ota.API, ott.Error, group_cancelled=cancelled, expected_sha256='a' * 64)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
