"""Bundle collection selects advertised evidence once, without probing or retrying."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_debug_cli import DebugClient, debug_snapshot, verify
from test_workbench_cli import Clock, RUNTIME, SECRET, ott, wb


def versioned_debug(version):
    value = debug_snapshot()
    value["version"] = version
    for lane in value["media"]:
        if version >= 2:
            lane["engine"] = "hls.js"
        if version == 3:
            lane["attempt"] = {"attachment": 1, "engineStarts": 2, "fatalErrors": 1,
                               "nativeFallbacks": 0, "streamRetries": 1}
            lane["lastAttempt"] = None
    return value


class NegotiatingClient(DebugClient):
    def __init__(self, clock, version):
        super().__init__(clock)
        if version >= 2:
            self.capabilities["debug_versions"] = [1, 2]
        if version == 3:
            self.capabilities["debug_attempts"] = {"version": 3}
        self.debug_failure = None
        self.debug_override = None

    def call_receipt(self, target, action, params):
        result = super().call_receipt(target, action, params)
        if action == "inspect" and params["section"] == "debug":
            if self.debug_failure is not None:
                raise self.debug_failure
            result["data"]["data"] = copy.deepcopy(
                self.debug_override if self.debug_override is not None else versioned_debug(params["version"]))
        return result


class BundleDebugNegotiationTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def execute(self, client, words):
        output = io.StringIO()
        with mock.patch.object(wb, "time", self.clock), contextlib.redirect_stdout(output):
            code = wb.run(ott, client, "web", words, 20, True)
        self.assertNotIn(SECRET, output.getvalue())
        self.assertTrue(all(call[1] in ("capabilities", "inspect", "maintenance") for call in client.calls))
        return code, json.loads(output.getvalue())

    def bundle(self, client, lane="web"):
        with tempfile.TemporaryDirectory() as directory:
            return self.execute(client, ["bundle", "--lane", lane, "--out", str(Path(directory).resolve() / "case")])

    @staticmethod
    def debug_calls(client):
        return [call for call in client.calls if call[1] == "inspect" and call[2]["section"] == "debug"]

    def test_bundle_selects_strongest_exact_advertised_version_and_retains_capabilities(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                client = NegotiatingClient(self.clock, version)
                code, report = self.bundle(client)
                self.assertEqual((code, report["evaluator"]), (0, "workbench-v4"))
                observation = report["debug_observations"][0]
                self.assertEqual(observation["status"], "observed")
                self.assertEqual(observation["data"], versioned_debug(version))
                self.assertEqual(observation["capabilities"], ott.capabilities_metadata(client.capabilities))
                self.assertEqual([call[2] for call in self.debug_calls(client)],
                                 [{"version": version, "runtime": RUNTIME, "section": "debug"}])
                # Snapshot collection has its existing three-quarter budget.
                self.assertEqual(client.calls[0][3], 15)
                self.assertEqual(client.calls[-1][3], 20)
                self.assertEqual(client.timeout, 20)

    def test_ordinary_debug_stays_v1_and_engine_keeps_explicit_negotiation(self):
        for advertised in (1, 2, 3):
            client = NegotiatingClient(self.clock, advertised)
            code, report = self.execute(client, ["debug"])
            self.assertEqual((code, report["observations"][0]["data"]["version"]), (0, 1))
            client.calls.clear()
            code, report = self.execute(client, ["debug", "--engine"])
            if advertised == 1:
                self.assertEqual((code, report["observations"][0]["reason"]), (3, "unsupported"))
                self.assertEqual(self.debug_calls(client), [])
            else:
                self.assertEqual((code, report["observations"][0]["data"]["version"]), (0, advertised))

    def test_missing_debug_capability_is_recorded_without_inspect_probe(self):
        client = NegotiatingClient(self.clock, 1)
        del client.capabilities["debug"]
        code, report = self.bundle(client)
        self.assertEqual(code, 0)
        self.assertEqual(report["debug_observations"][0]["reason"], "unsupported")
        self.assertEqual(self.debug_calls(client), [])

    def test_malformed_capabilities_do_not_trigger_version_probe(self):
        for change in ({"debug_versions": [1, 2, 3]}, {"debug_attempts": {"version": True}},
                       {"debug_attempts": {"version": 3, "private": SECRET}}):
            with self.subTest(change=change):
                client = NegotiatingClient(self.clock, 3)
                client.capabilities.update(change)
                _, report = self.bundle(client)
                self.assertEqual(report["debug_observations"][0]["status"], "unknown")
                self.assertNotIn("data", report["debug_observations"][0])
                self.assertEqual(self.debug_calls(client), [])

    def test_controller_rejection_keeps_unknown_without_retry_or_downgrade(self):
        client = NegotiatingClient(self.clock, 3)
        client.debug_failure = ott.HTTPError(400)
        code, report = self.bundle(client)
        self.assertEqual(code, 0)  # The independent snapshot remains useful.
        self.assertEqual(report["debug_observations"][0]["reason"], "unsupported_controller")
        self.assertEqual([call[2]["version"] for call in self.debug_calls(client)], [3])
        self.assertNotIn("data", report["debug_observations"][0])

    def test_wrong_response_version_or_runtime_is_rejected_without_retry(self):
        for change in ({"version": 1}, {"version": 2}, {"runtime": "replaced-page"}):
            with self.subTest(change=change):
                client = NegotiatingClient(self.clock, 3)
                client.debug_override = versioned_debug(3)
                client.debug_override.update(change)
                _, report = self.bundle(client)
                self.assertEqual(report["debug_observations"][0]["reason"], "invalid_response")
                self.assertNotIn("data", report["debug_observations"][0])
                self.assertEqual([call[2]["version"] for call in self.debug_calls(client)], [3])

    def test_native_only_bundle_never_reads_web_debug(self):
        client = NegotiatingClient(self.clock, 3)
        code, report = self.bundle(client, "native")
        self.assertEqual(code, 0)
        self.assertEqual(report["debug_observations"], [])
        self.assertTrue(all(call[0] == "native" for call in client.calls))

    def test_negotiated_bundles_verify_offline_including_unknown_debug(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                _, report = self.bundle(NegotiatingClient(self.clock, version))
                checked = verify.evaluate_report(report, ott, wb)
                self.assertEqual(checked["evaluation_policy"], "workbench-v4")
                self.assertEqual(checked["verdict"], "observed")
        client = NegotiatingClient(self.clock, 3)
        client.debug_failure = ott.HTTPError(400)
        _, report = self.bundle(client)
        self.assertEqual(verify.evaluate_report(report, ott, wb)["verdict"], "observed")
        self.assertEqual(report["debug_observations"][0]["status"], "unknown")

    def test_verifier_rejects_v4_downgrade_but_preserves_historical_v3(self):
        _, report = self.bundle(NegotiatingClient(self.clock, 3))
        report["debug_observations"][0]["data"] = versioned_debug(1)
        with self.assertRaises((verify.InvalidReport, wb.InvalidData)):
            verify.evaluate_report(report, ott, wb)
        report["evaluator"] = "workbench-v3"
        checked = verify.evaluate_report(report, ott, wb)
        self.assertEqual((checked["evaluation_policy"], checked["verdict"]), ("workbench-v3", "observed"))


if __name__ == "__main__":
    unittest.main()
