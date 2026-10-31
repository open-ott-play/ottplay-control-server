"""Offline provider reports validate bounded evidence and recompute correlation."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_report_verify import STAMP, fixtures, observed, report, verify
from test_bundle_debug_negotiation import versioned_debug


ott, wb = fixtures.ott, fixtures.wb
PUBLIC_TEST_MARKER = "invalid-report-fixture.example/public-marker"


def provider_data(runtime=fixtures.RUNTIME):
    return {"version": 1, "runtime": runtime, "capturedAt": 1000000,
            "available": True, "provider": "torrent",
            "catalog": {"view": "streams", "loadedCount": 20, "nextCursor": None,
                        "busy": False, "autoPages": False, "selectedIndex": 0},
            "lookup": {"id": 1, "ageMs": 1000, "state": "failed", "movieIdKind": "imdb",
                       "addonCount": 2, "failedAddons": 1, "skippedAddons": 1,
                       "unsupportedCount": 0, "playableCount": 0},
            "requests": [{"id": 1, "lookupId": 1, "addonIndex": 1, "stage": "stream",
                          "outcome": "http_error", "httpStatus": 400, "ageMs": 500,
                          "durationMs": 25, "responseBytes": None}], "requestsDropped": 0}


def provider_row(runtime=fixtures.RUNTIME):
    caps = fixtures.capabilities()
    caps["provider_diagnostics"] = {"version": 1}
    caps["player"]["runtime"] = runtime
    return {"lane": "web", "source": "runtime_reported", "status": "observed", "observed_at": STAMP,
            "runtime": runtime, "capabilities": caps, "data": provider_data(runtime),
            "requests": [{"request_id": format(5, "032x"), "action": "capabilities", "status": "ok"},
                         {"request_id": format(6, "032x"), "action": "status", "status": "ok"}]}


def provider_report(command="bundle"):
    value = report()
    debug = observed(serial=3)
    debug["data"] = versioned_debug(3)
    debug["capabilities"].update(debug={"version": 1}, debug_versions=[1, 2], debug_attempts={"version": 3})
    value.update(command=command, evaluator="workbench-v5", observations=[observed()],
                 debug_observations=[debug], provider_observations=[provider_row()])
    return value


class ProviderDiagnosticsReportTests(unittest.TestCase):
    def invoke(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "report"
            wb.write_report(path, value)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), \
                    mock.patch.object(ott, "Client", side_effect=AssertionError("network forbidden")), \
                    mock.patch.object(ott, "read_json", side_effect=AssertionError("configuration forbidden")):
                code = verify.main(["verify", str(path), "--json"], ott, wb)
        text = output.getvalue()
        self.assertNotIn(PUBLIC_TEST_MARKER, text)
        return code, json.loads(text)

    def test_bundle_doctor_and_debug_replay_without_network(self):
        for command in ("bundle", "doctor", "debug"):
            with self.subTest(command=command):
                code, result = self.invoke(provider_report(command))
                self.assertEqual((code, result["verdict"], result["provider_correlation"]),
                                 (0, "observed", "matched"))
                self.assertEqual(result["evaluation_policy"], "workbench-v5")
                self.assertEqual(result["report_kind"], command)
                self.assertFalse(result["authenticated"])

    def test_inactive_provider_is_unknown_for_doctor_but_snapshot_bundle_remains_observed(self):
        for command in ("bundle", "doctor", "debug"):
            value = provider_report(command)
            value["provider_observations"][0]["data"].update(
                available=False, provider=None, catalog=None, lookup=None, requests=[], requestsDropped=0)
            value["verdict"] = "observed" if command == "bundle" else "unknown"
            self.assertEqual(self.invoke(value)[0], 0 if command == "bundle" else 3)

    def test_transport_unknown_with_safe_receipts_is_not_success(self):
        value = provider_report("doctor")
        row = provider_row()
        row.pop("runtime")
        row.pop("data")
        row.update(status="unknown", reason="request_pending_timeout")
        row["requests"][-1]["status"] = "unconfirmed"
        value.update(provider_observations=[row], verdict="unknown")
        code, result = self.invoke(value)
        self.assertEqual((code, result["provider_correlation"]), (3, "unavailable"))
        value["verdict"] = "observed"
        self.assertEqual(self.invoke(value)[1]["reason"], "semantic_mismatch")

    def test_runtime_mixing_is_recomputed_and_forged_flags_are_rejected(self):
        value = provider_report()
        value["provider_observations"] = [provider_row("new-runtime")]
        code, result = self.invoke(value)
        self.assertEqual((code, result["provider_correlation"]), (0, "mixed"))
        value["provider_correlation"] = "matched"
        self.assertEqual(self.invoke(value)[0], 1)
        value.pop("provider_correlation")
        value["provider_observations"][0]["fresh"] = True
        self.assertEqual(self.invoke(value)[0], 1)

    def test_dto_runtime_and_lookup_request_identity_must_match(self):
        for change in (lambda data: data.update(runtime="different-runtime"),
                       lambda data: data["requests"][0].update(lookupId=2)):
            value = provider_report()
            change(value["provider_observations"][0]["data"])
            self.assertEqual(self.invoke(value)[0], 1)

    def test_unknown_fields_and_raw_status_never_enter_provider_evidence(self):
        paths = [(), ("capabilities",), ("data",), ("data", "lookup"), ("data", "requests", 0),
                 ("requests", 0)]
        for path in paths:
            value = provider_report()
            target = value["provider_observations"][0]
            for key in path:
                target = target[key]
            target["private_config"] = PUBLIC_TEST_MARKER
            self.assertEqual(self.invoke(value)[0], 1, path)
        value = provider_report()
        value["provider_observations"][0]["data"] = {"diagnostics": {"provider": provider_data()}}
        self.assertEqual(self.invoke(value)[0], 1)

    def test_unnegotiated_and_invalid_receipt_chains_are_rejected(self):
        changes = [lambda row: row["capabilities"].pop("provider_diagnostics"),
                   lambda row: row["requests"][1].update(action="inspect"),
                   lambda row: row["requests"][1].update(status="unconfirmed"),
                   lambda row: row["requests"][0].update(request_id=format(1, "032x")),
                   lambda row: row["requests"].reverse(),
                   lambda row: row["requests"].pop()]
        for change in changes:
            value = provider_report()
            change(value["provider_observations"][0])
            self.assertEqual(self.invoke(value)[0], 1)

    def test_oversized_and_nonfinite_provider_evidence_is_rejected(self):
        value = provider_report()
        value["provider_observations"][0]["data"]["requests"] *= 17
        self.assertEqual(self.invoke(value)[0], 1)
        for field, number in (("capturedAt", -1), ("capturedAt", True), ("requestsDropped", -1)):
            value = provider_report()
            value["provider_observations"][0]["data"][field] = number
            self.assertEqual(self.invoke(value)[0], 1)

    def test_native_only_bundle_keeps_provider_unavailable(self):
        value = provider_report()
        value.update(observations=[observed("native")], provider_observations=[], debug_observations=[])
        code, result = self.invoke(value)
        self.assertEqual((code, result["provider_correlation"]), (0, "unavailable"))

    def test_legacy_bundles_keep_existing_policy_and_do_not_require_provider_data(self):
        for policy in ("workbench-v2", "workbench-v3", "workbench-v4"):
            value = provider_report()
            value["evaluator"] = policy
            value.pop("provider_observations")
            if policy == "workbench-v2":
                value.pop("debug_observations")
            elif policy == "workbench-v3":
                debug = value["debug_observations"][0]
                debug["data"] = versioned_debug(1)
            code, result = self.invoke(value)
            self.assertEqual((code, result["evaluation_policy"]), (0, policy))
            self.assertNotIn("provider_correlation", result)

    def test_legacy_policies_cannot_claim_unvalidated_provider_evidence(self):
        for policy in ("workbench-v2", "workbench-v3", "workbench-v4"):
            for field, content in (("provider_observations", [{"raw_status": PUBLIC_TEST_MARKER}]),
                                   ("provider_correlation", "matched")):
                value = report()
                value["evaluator"] = policy
                if policy in ("workbench-v3", "workbench-v4"):
                    value["debug_observations"] = provider_report()["debug_observations"]
                    if policy == "workbench-v3":
                        value["debug_observations"][0]["data"] = versioned_debug(1)
                value[field] = content
                self.assertEqual(self.invoke(value)[0], 1)


if __name__ == "__main__":
    unittest.main()
