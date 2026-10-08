"""Reject impossible native evidence before it reaches live or saved reports."""
import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("native_evidence_ott", Path(__file__).resolve().parents[1] / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


def system_evidence(observed=False):
    value = {"app_pid": 123, "captured_uptime_seconds": 90.25,
             "physical_display_verified": False, "physical_audio_verified": False,
             "surface": {"state": "unavailable", "reason": "app_surface_unavailable"},
             "audio": {"state": "unavailable", "reason": "audio_service_unavailable"}}
    if observed:
        value["surface"] = {"state": "observed", "scope": "app_surface", "period_ns": 16666666,
                            "latest_present_ns": 80000000000, "completed_frames": 127, "video_verified": False}
        value["audio"] = {"state": "observed", "scope": "app_process", "audible_verified": False,
                          "tracks": [{"session_id": 1, "sample_rate": 48000, "server_frames": 4294967295,
                                      "underrun_frames": 0, "active": True}]}
    return value


def health():
    return {"version": 1, "runtime": "native-current", "webview_responsive": True, "app_pid": 123}


def receipt():
    return {"request_id": "a" * 32, "action": "lifecycle", "operation": "reload_player",
            "runtime": "native-historical", "boot_id": "11111111-1111-1111-1111-111111111111",
            "updated_at": 12345, "state": "handler_completed", "evidence": "handler_completed"}


class NativeEvidenceValidation(unittest.TestCase):
    def project(self, value):
        return ott.android_metadata(value, "maintenance", {"operation": "health"})

    def test_old_health_and_complete_fallback_remain_readable(self):
        self.assertEqual(self.project(health()), health())
        for observed in (False, True):
            value = {**health(), "system_evidence": system_evidence(observed)}
            self.assertEqual(self.project(value), value)
        value = {**health(), "app_pid": 0, "system_evidence": {**system_evidence(), "app_pid": 0}}
        self.assertEqual(self.project(value), value)

    def test_real_process_changed_shape_makes_no_pid_or_clock_claim(self):
        changed = {"state": "unavailable", "reason": "process_changed"}
        value = {**health(), "system_evidence": {"surface": changed, "audio": changed}}
        self.assertEqual(self.project(value), value)
        for extra in ({"app_pid": 123}, {"captured_uptime_seconds": 90}, {"physical_display_verified": False}):
            with self.subTest(extra=extra), self.assertRaises(ott.Error):
                self.project({**value, "system_evidence": {**value["system_evidence"], **extra}})
        value["system_evidence"]["audio"] = system_evidence()["audio"]
        with self.assertRaises(ott.Error):
            self.project(value)

    def test_system_evidence_requires_matching_integral_pid(self):
        for top, nested in ((123, 999), (None, 123), (True, 1), (123.0, 123), (123, None),
                            (0, 0), (2147483648, 2147483648)):
            value = {**health(), "app_pid": top, "system_evidence": {**system_evidence(True), "app_pid": nested}}
            with self.subTest(top=top, nested=nested), self.assertRaises(ott.Error):
                self.project(value)

    def test_observed_fields_cannot_be_missing_or_null(self):
        full = {**health(), "system_evidence": system_evidence(True)}
        for section in (None, "surface", "audio"):
            target = full["system_evidence"] if section is None else full["system_evidence"][section]
            for key in target:
                for missing in (False, True):
                    value = copy.deepcopy(full)
                    row = value["system_evidence"] if section is None else value["system_evidence"][section]
                    if missing:
                        del row[key]
                    else:
                        row[key] = None
                    with self.subTest(section=section, key=key, missing=missing), self.assertRaises(ott.Error):
                        self.project(value)

    def test_surface_counts_and_timestamps_are_exact_integers(self):
        for key, bad in (("period_ns", 0), ("period_ns", 1000000001), ("period_ns", 1.5),
                         ("latest_present_ns", 0), ("latest_present_ns", 9007199254740991),
                         ("latest_present_ns", True), ("completed_frames", 0),
                         ("completed_frames", 1.5), ("video_verified", True)):
            value = {**health(), "system_evidence": system_evidence(True)}
            value["system_evidence"]["surface"][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ott.Error):
                self.project(value)

    def test_audio_tracks_require_real_bounded_complete_uint32_records(self):
        for tracks in ([], [None], [{}], system_evidence(True)["audio"]["tracks"] * 17):
            value = {**health(), "system_evidence": system_evidence(True)}
            value["system_evidence"]["audio"]["tracks"] = tracks
            with self.subTest(tracks=len(tracks)), self.assertRaises(ott.Error):
                self.project(value)
        for key, bad in (("session_id", None), ("sample_rate", 1.5), ("server_frames", 4294967296),
                         ("underrun_frames", -1), ("active", None), ("active", 1)):
            value = {**health(), "system_evidence": system_evidence(True)}
            value["system_evidence"]["audio"]["tracks"][0][key] = bad
            with self.subTest(key=key), self.assertRaises(ott.Error):
                self.project(value)

    def test_unavailable_evidence_cannot_smuggle_observed_fields(self):
        for section, key in (("surface", "completed_frames"), ("audio", "tracks")):
            value = {**health(), "system_evidence": system_evidence()}
            value["system_evidence"][section][key] = None
            with self.subTest(section=section), self.assertRaises(ott.Error):
                self.project(value)
        value = {**health(), "system_evidence": system_evidence()}
        value["system_evidence"]["surface"]["reason"] = "audio_service_unavailable"
        with self.assertRaises(ott.Error):
            self.project(value)

    def test_decoder_allows_unknown_counters_not_unknown_false_claims(self):
        decoder = {"source": "html_video", "decoded_frames": None, "dropped_frames": None,
                   "audio_decoded_bytes": None, "volume": None, "muted": None,
                   "presented_frames": None, "audible_verified": False}
        value = {**health(), "player": {"decoder": decoder}}
        self.assertEqual(self.project(value), value)
        for key, bad in (("audible_verified", None), ("audible_verified", True),
                         ("presented_frames", 1), ("volume", 1.1), ("decoded_frames", float("nan"))):
            changed = copy.deepcopy(value); changed["player"]["decoder"][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ott.Error):
                self.project(changed)
        del value["player"]["decoder"]["muted"]
        with self.assertRaises(ott.Error):
            self.project(value)

    def test_identity_availability_has_conditional_fields(self):
        identity = {"version": 1, "available": False, "web_runtime": None, "generation": None,
                    "handle_id": None, "captured_at": 0, "consistent": False, "kind": "unknown"}
        value = {**health(), "player": {"identity": identity}}
        self.assertEqual(self.project(value), value)
        for key, bad in (("generation", 1), ("consistent", True), ("captured_at", 0.5), ("version", True)):
            changed = copy.deepcopy(value); changed["player"]["identity"][key] = bad
            with self.subTest(key=key), self.assertRaises(ott.Error):
                self.project(changed)
        identity.update(available=True, web_runtime="page-old", generation=1, handle_id=2,
                        captured_at=1000, consistent=True, kind="live")
        self.assertEqual(self.project(value), value)
        for key, bad in (("web_runtime", None), ("generation", 1.5), ("handle_id", True), ("captured_at", 0)):
            changed = copy.deepcopy(value); changed["player"]["identity"][key] = bad
            with self.subTest(key=key), self.assertRaises(ott.Error):
                self.project(changed)

    def test_durable_receipt_retains_historical_executor_and_validates_operation_pair(self):
        row = receipt()
        value = {**health(), "boot_id": "22222222-2222-2222-2222-222222222222", "operations": [row]}
        result = ott.native_operation_metadata(value, row["request_id"])
        self.assertEqual(result["receipt"], row)
        self.assertFalse(result["effect_observed"])
        # There is no trusted device/host clock correspondence at this boundary.
        row["updated_at"] = 9007199254740991
        self.assertEqual(self.project(value)["operations"][0]["updated_at"], row["updated_at"])
        for action, operation in (("lifecycle", "play"), ("playback", "reboot_device"),
                                  ("vportal_queue", "seek"), ("maintenance", "wake")):
            bad = {**row, "action": action, "operation": operation}
            with self.subTest(action=action), self.assertRaisesRegex(ott.Error, "Invalid native operation receipt"):
                self.project({**health(), "operations": [bad]})

    def test_receipt_timestamps_completion_and_history_inventory_are_validated(self):
        for key, bad in (("updated_at", 0), ("updated_at", 1.5), ("updated_at", True),
                         ("updated_at", 9007199254740992), ("boot_id", None),
                         ("evidence", "none"), ("state", "accepted")):
            row = {**receipt(), key: bad}
            with self.subTest(key=key, bad=bad), self.assertRaises(ott.Error):
                ott.native_operation_metadata({**health(), "operations": [row]}, "a" * 32)
        for rows in ([receipt(), receipt()], [None], [receipt()] * 65, None):
            with self.subTest(rows_type=type(rows).__name__), self.assertRaises(ott.Error):
                self.project({**health(), "operations": rows})
        row = receipt(); row.pop("boot_id")
        self.assertEqual(self.project({**health(), "operations": [row]})["operations"], [row])

    def test_unknown_private_fields_are_still_projected_out(self):
        value = {**health(), "system_evidence": system_evidence(True), "operations": [receipt()]}
        value["system_evidence"]["private_url"] = "secret"
        value["system_evidence"]["audio"]["tracks"][0]["private_url"] = "secret"
        value["operations"][0]["private_url"] = "secret"
        self.assertNotIn("secret", repr(self.project(value)))


if __name__ == "__main__":
    unittest.main()
