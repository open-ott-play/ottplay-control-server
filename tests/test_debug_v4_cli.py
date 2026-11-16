"""Passive attempt-error evidence is closed, negotiated, and backwards compatible."""
import contextlib
import copy
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

import test_debug_cli as legacy
from test_workbench_cli import RUNTIME, SECRET, ott, wb

ROOT = Path(__file__).resolve().parents[1]


def error(**changes):
    return dict(sequence=1, elapsedMs=0.25, engine="hls.js", type="media", detail="buffer_append_error",
                fatal=True, httpStatus=None, mediaErrorCode=None, **changes)


def v4_snapshot():
    value = copy.deepcopy(legacy.V3["response"]["data"])
    value["version"] = 4
    for lane in value["media"]:
        for name in ("attempt", "lastAttempt"):
            if lane[name] is not None:
                lane[name]["errors"] = {"firstFatal": error(), "latest": error(), "observed": 1, "dropped": 0}
        if lane["lastAttempt"] is not None:
            lane["lastAttempt"]["reason"] = "failed"
    return value


class DebugV4CLITest(unittest.TestCase):
    setUp = legacy.DebugCLITest.setUp
    run_debug = legacy.DebugCLITest.run_debug
    enable_attempts = legacy.DebugCLITest.enable_attempts

    def enable_errors(self):
        self.enable_attempts()
        self.client.capabilities["debug_attempt_errors"] = {"version": 4}
        self.client.debug = v4_snapshot()

    def test_v4_negotiates_once_and_preserves_error_evidence(self):
        self.enable_errors()
        code, result = self.run_debug(["debug", "--engine"])
        self.assertEqual(code, 0)
        self.assertEqual(self.client.calls[-1][2], {"version": 4, "runtime": RUNTIME, "section": "debug"})
        self.assertEqual(result["observations"][0]["data"], self.client.debug)
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])

    def test_v4_zero_null_and_closed_code_ranges(self):
        self.enable_errors()
        for lane in self.client.debug["media"]:
            for name in ("attempt", "lastAttempt"):
                if lane[name]:
                    lane[name]["errors"] = {"firstFatal": None, "latest": None, "observed": 0, "dropped": 0}
        self.assertEqual(self.run_debug(["debug", "--engine"])[0], 0)
        self.enable_errors()
        row = self.client.debug["media"][0]["attempt"]["errors"]
        for detail in wb.DEBUG_ERROR_DETAILS:
            for item in (row["firstFatal"], row["latest"]):
                item.update(detail=detail, elapsedMs=None, httpStatus=599, mediaErrorCode=4)
            self.assertEqual(self.run_debug(["debug", "--engine"])[0], 0)
        self.client.debug["media"][0].update(attempt=None, lastAttempt=None)
        self.assertEqual(self.run_debug(["debug", "--engine"])[0], 0)

    def test_v4_marker_is_strict_and_requires_unchanged_dependencies(self):
        self.enable_errors()
        for value in (None, {}, {"version": True}, {"version": 4.0}, {"version": 3}, {"version": 5},
                      {"version": 4, "url": SECRET}):
            with self.subTest(value=value), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(self.client.capabilities, debug_attempt_errors=value))
        for key in ("debug", "debug_versions", "debug_attempts"):
            caps = copy.deepcopy(self.client.capabilities)
            del caps[key]
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(caps)

    def test_pinned_actual_v3_parser_ignores_v4_marker_and_selects_v3(self):
        self.enable_errors()
        path = ROOT / "tests/fixtures/capabilities_metadata_539c999.txt"
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "f42cafd66da4b6ce4cc6b96f946304900420648417b1916231fb584544cd036e")
        namespace = dict(vars(ott))
        exec(compile(raw, str(path), "exec"), namespace)
        old_caps = namespace["capabilities_metadata"](self.client.capabilities)
        self.assertNotIn("debug_attempt_errors", old_caps)
        self.assertEqual(old_caps["debug_attempts"], {"version": 3})
        self.assertEqual(wb.strongest_debug_version(old_caps), 3)
        modern_caps = ott.capabilities_metadata(self.client.capabilities)
        modern_caps.pop("debug_attempt_errors")
        self.assertEqual(modern_caps, old_caps)

    def test_v4_invalid_errors_are_redacted_and_never_downgrade(self):
        changes = [lambda d: d.update(version=3),
                   lambda d: d["media"][0]["attempt"].pop("errors"),
                   lambda d: d["media"][0]["attempt"].update(errors=None)]
        for field in ("sequence", "elapsedMs", "httpStatus", "mediaErrorCode"):
            for bad in (True, -1, float("nan"), float("inf"), SECRET, 2 ** 53):
                changes.append(lambda d, field=field, bad=bad: d["media"][0]["attempt"]["errors"]["latest"].update({field: bad}))
        for field, bad in (("engine", SECRET), ("type", SECRET), ("detail", SECRET), ("fatal", 1),
                           ("sequence", 0), ("sequence", 0.5), ("httpStatus", 99), ("httpStatus", 600),
                           ("mediaErrorCode", 0), ("mediaErrorCode", 5), ("message", SECRET), ("url", SECRET)):
            changes.append(lambda d, field=field, bad=bad: d["media"][0]["attempt"]["errors"]["latest"].update({field: bad}))
        for field in ("observed", "dropped"):
            for bad in (True, -1, 0.5, float("nan"), 2 ** 53, SECRET):
                changes.append(lambda d, field=field, bad=bad: d["media"][0]["attempt"]["errors"].update({field: bad}))
        changes.extend([
            lambda d: d["media"][0]["attempt"]["errors"].update(observed=0),
            lambda d: d["media"][0]["attempt"]["errors"].update(dropped=2),
            lambda d: d["media"][0]["attempt"]["errors"].update(latest=None),
            lambda d: d["media"][0]["attempt"]["errors"]["firstFatal"].update(fatal=False),
            lambda d: d["media"][0]["attempt"]["errors"]["latest"].update(sequence=2),
            lambda d: d["media"][0]["attempt"]["errors"].update(stack=SECRET),
        ])
        for change in changes:
            self.enable_errors()
            self.client.calls.clear()
            change(self.client.debug)
            code, result = self.run_debug(["debug", "--engine"])
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
            self.assertNotIn("data", result["observations"][0])
            self.assertEqual([call[2]["version"] for call in self.client.calls if call[1] == "inspect"], [4])

    def test_v4_controller_rejection_is_not_retried(self):
        self.enable_errors()
        self.client.inspect_failure = ott.HTTPError(400)
        code, result = self.run_debug(["debug", "--engine"])
        self.assertEqual((code, result["observations"][0]["reason"]), (3, "unsupported_controller"))
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])
        self.assertEqual(self.client.calls[-1][2]["version"], 4)

    def test_shared_wire_fixture_and_saturating_sequences(self):
        fixture = json.loads((ROOT / "contracts/runtime-debug-v4.json").read_text())
        response = fixture["response"]
        self.assertEqual(wb.debug_metadata(response, response["runtime"], 4), response["data"])
        self.enable_errors()
        errors = self.client.debug["media"][0]["attempt"]["errors"]
        errors["latest"]["detail"] = "other"
        code, result = self.run_debug(["debug", "--engine"])
        self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
        errors["observed"] = wb.SAFE_INTEGER
        errors["firstFatal"]["sequence"] = errors["latest"]["sequence"] = wb.SAFE_INTEGER
        self.assertEqual(self.run_debug(["debug", "--engine"])[0], 0)

    def test_readable_v4_summary_reports_evidence_without_root_cause_claim(self):
        self.enable_errors()
        output = io.StringIO()
        with mock.patch.object(wb, "time", self.clock), contextlib.redirect_stdout(output):
            self.assertEqual(wb.run(ott, self.client, "web", ["debug", "--engine"], 20), 0)
        text = output.getvalue()
        self.assertIn("firstFatal: hls.js; media; buffer_append_error; fatal=true", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("root cause", text.lower())


if __name__ == "__main__":
    unittest.main()
