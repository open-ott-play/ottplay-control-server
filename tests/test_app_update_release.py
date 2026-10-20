"""High-level Android OTA pins official release identity and never replays install."""
import copy
import importlib.util
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
        import time
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read1(self, size):
                time.sleep(0.2)
                return b"{}"
        fake = SimpleNamespace(open=lambda *args, **kwargs: Response())
        started = time.monotonic()
        with patch.object(ota.urllib.request, "build_opener", return_value=fake), self.assertRaises(ott.Error):
            ota.request_json(ota.API, ott.Error, deadline=time.monotonic() + 0.02)
        self.assertLess(time.monotonic() - started, 0.15)

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
