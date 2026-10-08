"""Offline report verification: real writers, hostile files, no player access."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load("report_workbench_fixtures", ROOT / "tests/test_workbench_cli.py")
ott, wb = fixtures.ott, fixtures.wb
verify = load("report_verifier", ROOT / "cli/report_verify.py")
STAMP = "2026-10-08T12:00:00+00:00"
SECRET = "never-echo-secret-token-or-path"


def observed(lane="web", position=10, captured=1000000, serial=1):
    row = {"lane": lane, "source": "runtime_reported", "observed_at": STAMP, "status": "observed",
           "requests": [{"request_id": format(serial, "032x"), "action": "capabilities" if lane == "web" else "maintenance", "status": "ok"}]}
    if lane == "web":
        row.update(runtime=fixtures.RUNTIME, capabilities=fixtures.capabilities(), data=fixtures.snapshot(position, captured))
        row["requests"].append({"request_id": format(serial + 1, "032x"), "action": "inspect", "status": "ok"})
    else:
        data = wb.native_metadata(ott, fixtures.native(), "health")
        row.update(runtime=data["runtime"], data=data)
    return row


def unknown(lane="native", reason="not_bound"):
    return {"lane": lane, "source": "runtime_reported", "observed_at": STAMP,
            "status": "unknown", "reason": reason, "requests": []}


def report(command="bundle", scenario=None):
    value = {"version": 1, "command": command, "device_id": "device-one", "started_at": STAMP,
             "completed_at": STAMP, "read_only": True, "physical_display_verified": False,
             "tooling": {"workbench_sha256": "a" * 64, "cli_sha256": "b" * 64},
             "observations": [observed(), unknown()], "verdict": "observed"}
    if scenario:
        value.update(scenario=scenario, verdict="pass")
    if scenario == "media-progress":
        value.update(reason="same_media_progress_observed", duration_seconds=5,
                     final_observations=[observed(position=15, captured=1005000, serial=3), unknown()],
                     sample_interval_seconds={"min": 5, "max": 5}, evidence_level="decoder_progress_only")
    return value


class ReportVerifyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "case"

    def save(self, value=None):
        wb.write_report(self.path, report() if value is None else value)

    def rewrite(self, raw):
        (self.path / "result.json").write_bytes(raw)
        manifest = {"version": 1, "complete": True, "physical_display_verified": False,
                    "files": [{"name": "result.json", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]}
        (self.path / "manifest.json").write_bytes(wb.encoded(manifest))

    def invoke(self, path=None, argv=None, global_json=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(ott, "Client", side_effect=AssertionError("network forbidden")), \
                mock.patch.object(ott, "read_json", side_effect=AssertionError("config forbidden")):
            code = verify.main(argv or ["verify", str(path or self.path), "--json"], ott, wb, global_json)
        text = output.getvalue()
        self.assertNotIn(SECRET, text)
        result = json.loads(text)
        self.assertIs(result["authenticated"], False)
        self.assertIs(result["physical_display_verified"], False)
        self.assertNotIn("device_id", result)
        self.assertNotIn("observations", result)
        return code, result

    def test_valid_legacy_bundle_and_all_unknown_bundle(self):
        self.save()
        code, value = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(value["valid"])
        self.assertEqual(value["integrity"], "verified")
        self.assertEqual(value["evaluation_policy"], "legacy-web-v1")
        value = report()
        value.update(observations=[unknown("web", "unavailable"), unknown()], verdict="unknown")
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[0], 3)

    def test_health_recomputes_pass_fail_unknown(self):
        value = report("test", "health")
        self.save(value)
        self.assertEqual(self.invoke()[0], 0)
        native = observed("native")
        native["data"]["webview_responsive"] = False
        value.update(observations=[native], verdict="fail")
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[0], 2)
        value.update(observations=[unknown("web", "unavailable")], verdict="unknown")
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[0], 3)

    def test_progress_recomputes_pass_fail_and_unknown(self):
        value = report("test", "media-progress")
        self.save(value)
        self.assertEqual(self.invoke()[0], 0)
        value["final_observations"][0]["data"]["media"]["lanes"][0]["position"] = 10
        value.update(verdict="fail", reason="media_did_not_progress")
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[0], 2)
        value["final_observations"][0]["data"]["media"]["generation"] += 1
        value.update(verdict="unknown", reason="media_identity_changed_or_unavailable")
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[0], 3)

    def test_valid_hash_does_not_hide_semantic_mismatch(self):
        self.save()
        value = report("test", "media-progress")
        for verdict, reason in (("fail", "same_media_progress_observed"), ("pass", "media_did_not_progress")):
            with self.subTest(verdict=verdict, reason=reason):
                value.update(verdict=verdict, reason=reason)
                self.rewrite(wb.encoded(value))
                code, result = self.invoke()
                self.assertEqual((code, result["integrity"], result["reason"]), (1, "verified", "semantic_mismatch"))

    def test_legacy_native_progress_never_uses_new_native_evaluator(self):
        value = report("test", "media-progress")
        value.update(observations=[observed("native")], final_observations=[observed("native", serial=3)],
                     verdict="unknown", reason="media_identity_unavailable")
        self.save(value)
        with mock.patch.object(wb, "progress_verdict", side_effect=AssertionError("new evaluator forbidden")):
            self.assertEqual(self.invoke()[0], 3)
        value["verdict"] = "pass"
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[1]["reason"], "semantic_mismatch")

    def test_explicit_v2_native_progress_uses_production_predicate(self):
        value = report("test", "media-progress")
        before = wb.native_metadata(ott, fixtures.native_playing(), "health")
        after = wb.native_metadata(ott, fixtures.native_playing(15, 1005000, 65), "health")
        a, b = observed("native"), observed("native", serial=3)
        a.update(data=before, runtime=before["runtime"])
        b.update(data=after, runtime=after["runtime"])
        value.update(evaluator="workbench-v2", observations=[a], final_observations=[b],
                     reason="same_native_media_progress_observed")
        self.save(value)
        code, result = self.invoke()
        self.assertEqual((code, result["evaluation_policy"]), (0, "workbench-v2"))
        value["evaluator"] = "unreviewed-future-policy"
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[1]["reason"], "unsupported_evaluator")

    def test_unknown_fields_never_appear_in_summary(self):
        value = report()
        value[SECRET] = {"credentials": SECRET}
        value["observations"][0]["data"]["ui"][SECRET] = SECRET
        self.save(value)
        self.assertEqual(self.invoke()[0], 0)

    def test_request_ids_cannot_be_reused_across_samples_or_lanes(self):
        value = report("test", "media-progress")
        self.save(value)
        value["final_observations"][0]["requests"][1]["request_id"] = value["observations"][0]["requests"][1]["request_id"]
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[0], 1)
        value = report()
        value["observations"][1] = observed("native")
        self.rewrite(wb.encoded(value))
        self.assertEqual(self.invoke()[0], 1)

    def test_native_logs_allow_unknown_boot_but_not_false_runtime_match(self):
        value = report()
        row = observed("native")
        row["requests"].append({"request_id": "4" * 32, "action": "maintenance", "status": "ok"})
        row["logs"] = {"version": 1, "runtime_correlation": "matched", "unknown_events_omitted": 0,
                       "events": [{"time": 1, "event": "poll_failed", "runtime": row["runtime"], "boot_id": ""}]}
        value["observations"] = [row]
        self.save(value)
        self.assertEqual(self.invoke()[0], 0)
        for runtime in (None, "different-runtime"):
            if runtime is None:
                row["logs"]["events"][0].pop("runtime", None)
            else:
                row["logs"]["events"][0]["runtime"] = runtime
            self.rewrite(wb.encoded(value))
            self.assertEqual(self.invoke()[0], 1)

    def test_directory_inventory_is_bounded(self):
        self.save()
        yielded = []
        def entries():
            for index in range(1000):
                yielded.append(index)
                yield SimpleNamespace(name=str(index))
        with mock.patch.object(verify.os, "scandir", return_value=contextlib.nullcontext(entries())):
            self.assertEqual(self.invoke()[1]["reason"], "unexpected_report_files")
        self.assertEqual(len(yielded), 3)

    def test_invalid_observation_fields_cannot_create_pass(self):
        self.save()
        mutations = [
            lambda x: x.update(version=True),
            lambda x: x.update(physical_display_verified=True),
            lambda x: x.update(read_only=False),
            lambda x: x["observations"][0].update(reason="not_bound"),
            lambda x: x["observations"][0].update(runtime="different-runtime"),
            lambda x: x["observations"][0].update(requests=[]),
            lambda x: x["observations"][0]["data"].update(consistent=None),
            lambda x: x["observations"][0]["data"]["media"].update(displayEvidence="verified"),
            lambda x: x["observations"].append(copy.deepcopy(x["observations"][0])),
            lambda x: x["observations"][1].update(data={}),
            lambda x: x["observations"][1].update(reason=SECRET),
        ]
        for mutate in mutations:
            value = report("test", "health")
            mutate(value)
            self.rewrite(wb.encoded(value))
            with self.subTest(mutate=mutate):
                code, result = self.invoke()
                self.assertEqual(code, 1)
                self.assertEqual(result["integrity"], "verified")

    def test_manifest_shape_digest_and_count_are_enforced(self):
        self.save()
        original = json.loads((self.path / "manifest.json").read_text())
        mutations = [lambda x: x.update(complete=False), lambda x: x.update(version=True),
                     lambda x: x.update(extra=1), lambda x: x.update(physical_display_verified=True),
                     lambda x: x["files"][0].update(name="../" + SECRET),
                     lambda x: x["files"][0].update(name="/" + SECRET),
                     lambda x: x["files"][0].update(bytes=True),
                     lambda x: x["files"][0].update(bytes=x["files"][0]["bytes"] + 1),
                     lambda x: x["files"][0].update(sha256="0" * 64),
                     lambda x: x["files"].append(copy.deepcopy(x["files"][0]))]
        for mutate in mutations:
            value = copy.deepcopy(original)
            mutate(value)
            (self.path / "manifest.json").write_bytes(wb.encoded(value))
            code, result = self.invoke()
            self.assertEqual((code, result["integrity"]), (1, "invalid"))

    def test_strict_json_rejects_duplicates_nonfinite_utf8_and_depth(self):
        self.save()
        for raw in (b'{"version":1,"version":1}', b'{"x":NaN}', b'{"x":Infinity}',
                    b'{"x":1e999}', b'\xff', b'[' * 80 + b'0' + b']' * 80):
            with self.subTest(raw=raw[:20]):
                self.rewrite(raw)
                code, result = self.invoke()
                self.assertEqual((code, result["integrity"], result["reason"]), (1, "verified", "invalid_json"))
        (self.path / "manifest.json").write_bytes(b'{"version":1,"version":1}')
        self.assertEqual(self.invoke()[1]["integrity"], "invalid")

    def test_bounds_missing_and_extra_files(self):
        self.save()
        for name, size in (("result.json", verify.MAX_RESULT_BYTES + 1), ("manifest.json", verify.MAX_MANIFEST_BYTES + 1)):
            original = (self.path / name).read_bytes()
            (self.path / name).write_bytes(b" " * size)
            self.assertEqual(self.invoke()[1]["reason"], "report_size_invalid")
            (self.path / name).write_bytes(original)
        extra = self.path / "secret-extra"
        extra.write_text(SECRET)
        self.assertEqual(self.invoke()[1]["reason"], "unexpected_report_files")
        extra.unlink()
        (self.path / "result.json").unlink()
        self.assertEqual(self.invoke()[1]["reason"], "unexpected_report_files")

    def test_retained_handle_stability_does_not_mix_windows_stat_clocks(self):
        # CPython 3.12 Windows fstat uses ChangeTime; path stat uses birthtime.
        fields = dict(st_dev=1, st_ino=2, st_mode=0o100600, st_size=50,
                      st_mtime_ns=300, st_ctime_ns=400)
        handle_info = SimpleNamespace(**fields)
        path_info = SimpleNamespace(**dict(fields, st_ctime_ns=100))
        expected = verify._fingerprint(handle_info)
        self.assertNotEqual(expected, verify._fingerprint(path_info))
        with mock.patch.object(verify.os, "fstat", return_value=handle_info) as fstat, \
                mock.patch.object(verify.os, "stat", return_value=path_info) as path_stat, \
                mock.patch.object(verify.os, "lstat", return_value=path_info) as lstat:
            verify._verify_unchanged("result.json", 37, expected, None)
            fstat.assert_called_once_with(37)
            path_stat.assert_not_called()
            lstat.assert_not_called()
            for field in ("st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
                with self.subTest(field=field):
                    fstat.return_value = SimpleNamespace(**dict(fields, **{field: fields[field] + 1}))
                    with self.assertRaises(verify.InvalidReport) as raised:
                        verify._verify_unchanged("result.json", 37, expected, None)
                    self.assertEqual(raised.exception.reason, "report_changed")
            # The POSIX lane still detects a replaced pathname even if its
            # original open descriptor is unchanged.
            fstat.return_value = handle_info
            with self.assertRaises(verify.InvalidReport) as raised:
                verify._verify_unchanged("result.json", 37, expected, 38)
            self.assertEqual(raised.exception.reason, "report_changed")
            path_stat.assert_called_once_with("result.json", dir_fd=38, follow_symlinks=False)

    @unittest.skipUnless(os.name == "nt", "Windows retained-handle sharing contract")
    def test_windows_handles_block_writes_and_file_or_ancestor_rename(self):
        self.save()
        original_check = verify._verify_unchanged
        checks = []

        def sharing_violation(action, undo):
            try:
                action()
            except OSError as error:
                self.assertEqual(error.winerror, 32)
            else:
                undo()
                self.fail("Retained report handle allowed a conflicting operation")

        def check(name, fd, expected, directory_fd):
            self.assertIsNone(directory_fd)
            if not checks:
                for filename in ("manifest.json", "result.json"):
                    target = self.path / filename
                    moved = self.path / (filename + ".moved")
                    # Open without truncation so a failing guard cannot corrupt
                    # the fixture before the test reports its failure.
                    opened = []
                    sharing_violation(lambda: opened.append(os.open(target, os.O_WRONLY)),
                                      lambda: os.close(opened.pop()))
                    sharing_violation(lambda: target.rename(moved), lambda: moved.rename(target))
                moved_directory = self.root / "moved-case"
                sharing_violation(lambda: self.path.rename(moved_directory),
                                  lambda: moved_directory.rename(self.path))
            checks.append(name)
            original_check(name, fd, expected, directory_fd)

        with mock.patch.object(verify, "_verify_unchanged", side_effect=check):
            raw_manifest, raw_result = verify.read_bundle(str(self.path))
        verify.verify_manifest(raw_manifest, raw_result)
        self.assertEqual(checks, ["manifest.json", "result.json"])
        # ExitStack released every handle on successful completion.
        fd = os.open(self.path / "result.json", os.O_WRONLY)
        os.close(fd)
        moved_directory = self.root / "released-case"
        self.path.rename(moved_directory)
        moved_directory.rename(self.path)

    @unittest.skipUnless(os.name == "posix", "POSIX no-follow and FIFO contracts")
    def test_symlinks_ancestors_and_fifos_are_rejected_without_blocking(self):
        self.save()
        target = self.path / "result.json"
        saved = target.read_bytes()
        alternate = self.root / SECRET
        alternate.write_bytes(saved)
        target.unlink()
        target.symlink_to(alternate)
        self.assertEqual(self.invoke()[0], 1)
        target.unlink()
        os.mkfifo(target)
        self.assertEqual(self.invoke()[1]["reason"], "unsafe_report_path")
        target.unlink()
        target.write_bytes(saved)
        link = self.root / "link"
        link.symlink_to(self.path, target_is_directory=True)
        self.assertEqual(self.invoke(link)[0], 1)
        parent = self.root / "parent"
        parent.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.invoke(parent / "case")[0], 1)
        self.assertEqual(self.invoke(self.root / "unused" / ".." / "case")[1]["reason"], "unsafe_report_path")

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor race contract")
    def test_file_swap_cannot_redirect_read_to_symlink(self):
        self.save()
        outside = self.root / SECRET
        outside.write_text(SECRET)
        original_open = os.open
        def swap(path, flags, *args, **kwargs):
            if path == "result.json":
                (self.path / "result.json").unlink()
                (self.path / "result.json").symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(verify.os, "open", side_effect=swap):
            self.assertEqual(self.invoke()[0], 1)

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor race contract")
    def test_directory_swap_keeps_original_open_directory(self):
        self.save()
        original_open = os.open
        moved = self.root / "opened-case"
        changed = False
        def swap(path, flags, *args, **kwargs):
            nonlocal changed
            if path == "manifest.json" and not changed:
                changed = True
                self.path.rename(moved)
                self.path.symlink_to(self.root / SECRET, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(verify.os, "open", side_effect=swap):
            self.assertEqual(self.invoke()[0], 0)
        self.assertTrue(changed)

    def test_cli_dispatch_never_reads_configuration_or_creates_client(self):
        self.save()
        for args in (["report", "verify", str(self.path), "--json"],
                     ["--json", "-c", str(self.root / SECRET), "report", "verify", str(self.path)]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), \
                    mock.patch.object(ott, "read_json", side_effect=AssertionError("config forbidden")) as config, \
                    mock.patch.object(ott, "Client", side_effect=AssertionError("client forbidden")) as client:
                self.assertEqual(ott.main(args), 0)
                self.assertTrue(json.loads(output.getvalue())["valid"])
                config.assert_not_called()
                client.assert_not_called()

    def test_copied_seven_file_cli_subprocess_works_without_credentials(self):
        self.save()
        install = self.root / "install"
        install.mkdir()
        files = ("ott.py", "programme_search.py", "playlist_search.py", "diagnostics.py",
                 "diagnostics_mcp.py", "workbench.py", "report_verify.py")
        for name in files:
            shutil.copyfile(ROOT / "cli" / name, install / name)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run([sys.executable, "-B", str(install / "ott.py"), "--json", "-c", str(self.root / SECRET),
                                    "report", "verify", str(self.path)], capture_output=True, text=True, env=env, timeout=10, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["valid"])
        self.assertNotIn(SECRET, completed.stdout + completed.stderr)

    def test_real_workbench_writer_round_trips_bundle_health_and_progress(self):
        for index, words in enumerate((["bundle"], ["test", "run", "health"], ["test", "run", "media-progress"])):
            target = self.root / str(index)
            clock = fixtures.Clock()
            client = fixtures.Client(clock)
            args = [*words, "--out" if words[0] == "bundle" else "--report", str(target), "--json"]
            with mock.patch.object(wb, "time", clock), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(wb.run(ott, client, "web", args, 20), 0)
            self.assertEqual(self.invoke(target)[0], 0)

    def test_invalid_arguments_are_sanitized(self):
        code, result = self.invoke(argv=["verify", SECRET, "--unknown", "--json"])
        self.assertEqual((code, result["reason"]), (1, "invalid_arguments"))


if __name__ == "__main__":
    unittest.main()
