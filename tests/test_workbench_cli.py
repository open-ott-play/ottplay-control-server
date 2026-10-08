"""Workbench observations exercise validated transport and evidence, never repairs."""
import contextlib
import copy
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "cli" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load("workbench_ott", "ott.py")
wb = load("workbench_test", "workbench.py")
RUNTIME = "page-one"
SECRET = "do-not-export-private-provider-token"


def snapshot(position=10, captured=1000000):
    video = {"exists": True, "cssVisible": True, "rect": {"x": 0, "y": 0, "width": 1280, "height": 720},
             "paused": False, "ended": False, "readyState": 4, "networkState": 2, "videoWidth": 600, "videoHeight": 480}
    return {"version": 1, "runtime": RUNTIME, "capturedAt": captured, "collectionMs": 1,
            "consistent": True, "build": {"version": "1.1.53-beta.13", "sourceRevision": "a" * 40,
            "buildId": None, "identity": "partial"},
            "ui": {"documentVisibility": "visible", "documentFocused": True, "owner": None,
                   "revision": 5, "panes": [], "focus": "player"},
            "media": {"generation": 7, "kind": "vod", "phase": "playing", "displayEvidence": "unavailable",
                      "lanes": [{"lane": "main", "handleId": 9, "phase": "playing", "position": position,
                                 "duration": 600, "video": video}]},
            "capabilities": [{"name": "reload_player", "state": "available", "reason": "ready"}],
            "reasons": ["build_identity_partial", "physical_display_unverified"]}


def capabilities():
    return {"version": 1, "player": {"version": "1.1.53-beta.13", "platform": "browser", "runtime": RUNTIME},
            "lifecycle": ["reload_player"], "input": [], "playback": [],
            "inspect": {"version": 1, "sections": ["doctor", "snapshot", "operation"]}}


def native():
    return {"version": 1, "runtime": "android-one", "agent_version": "0.1.1", "app_pid": 123,
            "webview_responsive": True, "uptime_seconds": 60, "watchdog_suspended": False,
            "player": {"ready": True, "queue": {"index": 0, "total": 1, "items": [{"id": 1, "title": SECRET}]},
                       "video": {"position": 20, "paused": False, "source": SECRET}}, "private": SECRET}


def native_playing(position=10, captured=1000000, uptime=60):
    data = native()
    data.update(boot_id="12345678-1234-1234-1234-123456789abc", uptime_seconds=uptime,
                system_evidence={"app_pid": 123, "captured_uptime_seconds": uptime + 0.1,
                    "physical_display_verified": False, "physical_audio_verified": False,
                    "surface": {"state": "unavailable", "reason": "app_surface_unavailable"},
                    "audio": {"state": "unavailable", "reason": "audio_service_unavailable"}})
    data["player"].update(
        identity={"version": 1, "available": True, "consistent": True, "web_runtime": RUNTIME,
                  "generation": 7, "handle_id": 9, "kind": "vod", "captured_at": captured},
        decoder={"source": "html_video", "decoded_frames": None, "dropped_frames": None,
                 "audio_decoded_bytes": None, "volume": 1, "muted": False,
                 "presented_frames": None, "audible_verified": False},
        video={"position": position, "duration": 600, "paused": False, "ended": False,
               "ready": 4, "error": 0, "width": 600, "height": 480, "source": SECRET})
    return data


def native_observation(data):
    return {"lane": "native", "status": "observed", "data": wb.native_metadata(ott, data, "health")}


class Clock:
    def __init__(self):
        self.value = 1000

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Client:
    def __init__(self, clock, bound=True):
        self.clock, self.timeout, self.calls = clock, 20, []
        self.config = {"native_devices": {"web": "native"} if bound else {}}
        self.credentials = {"devices": [{"id": "web"}, {"id": "native"}]}
        self.snapshots = []
        self.capabilities = capabilities()
        self.inspect_failure = None
        self.inspect_envelope = None
        self.native = native()
        self.native_snapshots = []
        self.failure = None
        self.consume_web_timeout = False

    def device(self, _name):
        return "web"

    def call_receipt(self, target, action, params):
        self.calls.append((target, action, copy.deepcopy(params), self.timeout))
        if target == "web" and self.failure:
            if self.consume_web_timeout:
                self.clock.sleep(self.timeout)
            raise self.failure
        if action == "capabilities":
            data = copy.deepcopy(self.capabilities)
        elif action == "inspect":
            if self.inspect_failure:
                if self.consume_web_timeout:
                    self.clock.sleep(self.timeout)
                raise self.inspect_failure
            data = {"version": 1, "runtime": RUNTIME, "section": params["section"],
                    "data": self.snapshots.pop(0) if self.snapshots else snapshot(10 + self.clock.value - 1000, int(self.clock.value * 1000))}
            if params["section"] == "operation":
                data["data"] = {"operation_id": params["operation_id"], "state": "unknown", "action": None,
                                "evidence": {"kind": "none", "generation": None, "position": None}}
            if self.inspect_envelope is not None:
                data = copy.deepcopy(self.inspect_envelope)
        elif action == "maintenance" and params == {"operation": "health"}:
            data = copy.deepcopy(self.native_snapshots.pop(0) if self.native_snapshots else self.native)
        elif action == "maintenance" and params == {"operation": "logs"}:
            data = {"version": 1, "events": [{"time": 1, "event": "poll_failed"}, {"time": 2, "event": SECRET}]}
        else:
            raise AssertionError("Unexpected operation, possible mutation")
        return {"request_id": format(len(self.calls), "032x"), "status": "ok", "data": data}


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = Client(self.clock)

    def execute(self, words, client=None, timeout=20, global_json=False):
        out = io.StringIO()
        with mock.patch.object(wb, "time", self.clock), contextlib.redirect_stdout(out):
            code = wb.run(ott, client or self.client, "web", words, timeout, global_json)
        text = out.getvalue()
        return code, json.loads(text), text

    def test_doctor_combines_separate_read_only_lanes(self):
        code, result, text = self.execute(["doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["verdict"], "observed")
        self.assertEqual(result["evaluator"], "workbench-v2")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["physical_display_verified"])
        self.assertNotIn(SECRET, text)
        self.assertEqual([row["lane"] for row in result["observations"]], ["web", "native"])
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities", "inspect", "maintenance"])
        self.assertEqual(self.client.calls[1][2], {"version": 1, "runtime": RUNTIME, "section": "doctor"})
        self.assertEqual(self.client.timeout, 20)

    def test_native_only_remains_usable_with_offline_web(self):
        self.client.failure = ott.TransportError(SECRET)
        self.client.consume_web_timeout = True
        code, result, text = self.execute(["doctor", "--json"])
        self.assertEqual(code, 0)
        web, native_row = result["observations"]
        self.assertEqual(web["reason"], "unavailable")
        self.assertEqual(native_row["status"], "observed")
        self.assertEqual(self.client.calls[0][3], 10)
        self.assertEqual(self.client.calls[-1][3], 10)
        self.assertNotIn(SECRET, text)
        self.client.calls.clear()
        code, result, _ = self.execute(["inspect", "--lane", "native", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.client.calls), 1)
        self.assertFalse(result["observations"][0]["data"]["media_identity_available"])

    def test_unknown_fields_are_projected_out_and_view_limits_web_fields(self):
        data = snapshot()
        data["secret"] = data["ui"]["secret"] = data["media"]["lanes"][0]["secret"] = SECRET
        self.client.snapshots = [data]
        code, result, text = self.execute(["inspect", "--lane", "web", "--view", "ui", "-j"])
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("media", result["observations"][0]["data"])
        self.assertIn("ui", result["observations"][0]["data"])

    def test_invalid_and_mismatched_snapshots_never_escape(self):
        for mutate in (
            lambda data: data.update(runtime="another-page"),
            lambda data: data.update(version=True),
            lambda data: data.update(capturedAt=float("nan")),
            lambda data: data["media"].update(generation=True),
            lambda data: data["media"].update(lanes=data["media"]["lanes"] * 2),
            lambda data: data["ui"].update(focus=SECRET),
            lambda data: data.update(capabilities=data["capabilities"] * 11),
            lambda data: data.update(secret="x" * 8192),
        ):
            self.client.snapshots = [snapshot()]
            mutate(self.client.snapshots[0])
            code, result, text = self.execute(["inspect", "--lane", "web", "-j"])
            self.assertEqual(code, 3)
            row = result["observations"][0]
            self.assertEqual(row["reason"], "invalid_response")
            self.assertNotIn("data", row)
            self.assertNotIn(SECRET, text)

    def test_old_controller_and_native_binding_failure_have_distinct_reasons(self):
        self.client.failure = ott.HTTPError(400)
        self.client.config["native_devices"] = {"web": "web"}
        code, result, _ = self.execute(["doctor", "-j"])
        self.assertEqual(code, 3)
        self.assertEqual([row["reason"] for row in result["observations"]],
                         ["unsupported_controller", "invalid_native_binding"])
        self.assertEqual(len(self.client.calls), 1)

    def test_inspection_capability_is_validated_and_respected(self):
        caps = capabilities()
        caps["inspect"] = {"version": 1, "sections": ["doctor", "snapshot", "operation"]}
        self.assertEqual(ott.capabilities_metadata(caps)["inspect"], caps["inspect"])
        for invalid in ({"version": True, "sections": ["doctor"]}, {"version": 1, "sections": ["doctor", "doctor"]},
                        {"version": 1, "sections": ["private"]}, {"version": 1, "sections": []}):
            caps["inspect"] = invalid
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(caps)
        caps["inspect"] = {"version": 1, "sections": ["operation"]}
        with mock.patch.object(self.client, "call_receipt", return_value={"request_id": "a" * 32, "status": "ok", "data": caps}) as call:
            code, result, _ = self.execute(["doctor", "--lane", "web", "-j"])
        self.assertEqual((code, call.call_count), (3, 1))
        self.assertEqual(result["observations"][0]["reason"], "unsupported")

    def test_missing_inspection_capability_never_sends_probe_and_keeps_native_fallback(self):
        del self.client.capabilities["inspect"]
        code, result, _ = self.execute(["doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["reason"], "unsupported")
        self.assertEqual(result["observations"][1]["status"], "observed")
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "maintenance"])
        for words in (["inspect", "--lane", "web", "-j"], ["operation", "a" * 32, "-j"]):
            self.client.calls.clear()
            code, result, _ = self.execute(words)
            self.assertEqual(code, 3)
            self.assertEqual(result["observations"][0]["reason"], "unsupported")
            self.assertEqual([call[1] for call in self.client.calls], ["capabilities"])

    def test_new_player_old_controller_rejection_is_not_retried(self):
        self.client.inspect_failure = ott.HTTPError(400)
        code, result, _ = self.execute(["doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["reason"], "unsupported_controller")
        self.assertEqual(result["observations"][1]["status"], "observed")
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect", "maintenance"])

    def test_inspection_timeout_preserves_the_native_budget(self):
        self.client.inspect_failure = ott.TransportError(SECRET)
        self.client.consume_web_timeout = True
        code, result, text = self.execute(["doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["reason"], "unavailable")
        self.assertEqual(result["observations"][1]["status"], "observed")
        self.assertEqual([call[3] for call in self.client.calls], [10, 10, 10])
        self.assertEqual(self.client.timeout, 20)
        self.assertEqual(self.clock.value, 1010)
        self.assertNotIn(SECRET, text)

    def test_crossed_success_envelopes_are_never_attributed_to_current_page(self):
        for change in ({"runtime": "other-page"}, {"section": "operation"}, {"version": True},
                       {"error": "unavailable"}, {"secret": SECRET}):
            self.client.inspect_envelope = {"version": 1, "runtime": RUNTIME, "section": "doctor", "data": snapshot()}
            self.client.inspect_envelope.update(change)
            code, result, text = self.execute(["doctor", "--lane", "web", "--json"])
            self.assertEqual(code, 3)
            self.assertEqual(result["observations"][0]["reason"], "invalid_response")
            self.assertNotIn("data", result["observations"][0])
            self.assertNotIn(SECRET, text)

    def test_negative_envelopes_keep_runtime_and_section_fencing(self):
        for error_type in (ott.PlayerRejected, ott.PlayerUnsupported):
            for data, reason in (
                ({"version": 1, "runtime": RUNTIME, "section": "doctor", "error": "unavailable"}, "unavailable"),
                ({"version": 1, "runtime": "other-page", "section": "doctor", "error": "unavailable"}, "invalid_response"),
                ({"version": 1, "runtime": RUNTIME, "section": "snapshot", "error": "unavailable"}, "invalid_response"),
                ({"version": 1, "runtime": RUNTIME, "section": "doctor", "error": "unavailable", "data": None}, "invalid_response"),
                ({"version": 1, "runtime": RUNTIME, "section": "doctor", "error": SECRET}, "invalid_response"),
                ({"error": "unsupported"}, "invalid_response"),
            ):
                error = error_type(SECRET, data)
                error.request_id = "a" * 32
                self.client.inspect_failure = error
                code, result, text = self.execute(["doctor", "--lane", "web", "--json"])
                self.assertEqual(code, 3)
                row = result["observations"][0]
                self.assertEqual(row["reason"], reason)
                self.assertEqual(row["requests"][-1]["request_id"], "a" * 32)
                self.assertNotIn("data", row)
                self.assertNotIn(SECRET, text)

    def test_build_metadata_versions_are_supported_without_relaxing_runtime_ids(self):
        data = snapshot()
        data["build"]["version"] = "1.1.53-beta.9+plex-arrows"
        self.client.snapshots = [data]
        code, result, _ = self.execute(["doctor", "--lane", "web", "-j"])
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["data"]["build"]["version"], data["build"]["version"])
        caps = capabilities()
        caps["player"]["version"] = data["build"]["version"]
        self.assertEqual(ott.capabilities_metadata(caps)["player"]["version"], data["build"]["version"])
        data["runtime"] = "page+one"
        with self.assertRaises(wb.InvalidData):
            wb.snapshot_metadata({"version": 1, "runtime": "page+one", "section": "doctor", "data": data}, "page+one", "doctor")

    def test_operation_reads_one_exact_id_without_replaying_effect(self):
        operation_id = "a" * 32
        code, result, _ = self.execute(["operation", operation_id, "--json"])
        self.assertEqual(code, 3)
        self.assertEqual(result["operation_state"], "unknown")
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities", "inspect"])
        self.assertEqual(self.client.calls[-1][2], {"version": 1, "runtime": RUNTIME, "section": "operation", "operation_id": operation_id})
        self.assertNotEqual(result["operation_id"], result["observations"][0]["requests"][-1]["request_id"])

    def test_operation_cannot_claim_observed_from_admission_or_handler_completion(self):
        operation_id = "a" * 32
        raw = {"version": 1, "runtime": RUNTIME, "section": "operation", "data": {
            "operation_id": operation_id, "state": "observed", "action": "playback", "private": SECRET,
            "evidence": {"kind": "handler_completed", "generation": 1, "position": 2}}}
        with self.assertRaises(wb.InvalidData):
            wb.operation_metadata(raw, RUNTIME, operation_id)
        raw["data"]["evidence"]["kind"] = "media_progress"
        self.assertNotIn(SECRET, json.dumps(wb.operation_metadata(raw, RUNTIME, operation_id)))
        raw["data"]["evidence"]["generation"] = None
        with self.assertRaises(wb.InvalidData):
            wb.operation_metadata(raw, RUNTIME, operation_id)
        raw["data"]["evidence"].update(kind="none", position=None)
        raw["data"].update(state="unknown", action=None)
        self.assertEqual(wb.operation_metadata(raw, RUNTIME, operation_id)["state"], "unknown")
        with self.assertRaises(wb.InvalidData):
            wb.operation_metadata(raw, RUNTIME, "b" * 32)

    def test_server_negative_envelope_does_not_become_a_snapshot(self):
        for reason in ("invalid_request", "runtime_mismatch", "unsupported", "unavailable"):
            with self.assertRaises(wb.ObservationError) as caught:
                wb.snapshot_metadata({"version": 1, "runtime": RUNTIME, "section": "snapshot", "error": reason}, RUNTIME, "snapshot")
            self.assertEqual(caught.exception.reason, reason)

    def test_media_progress_requires_same_identity_and_decoder_state(self):
        first, second = snapshot(), snapshot(15, 1005000)
        def row(data):
            return [{"lane": "web", "status": "observed", "data": data}]
        self.assertEqual(wb.progress_verdict(row(first), row(second), (5, 5)), ("pass", "same_media_progress_observed"))
        for mutate in (
            lambda data: data.update(runtime="other"),
            lambda data: data.update(consistent=False),
            lambda data: data["media"].update(generation=None),
            lambda data: data["media"].update(generation=8),
            lambda data: data["media"]["lanes"][0].update(handleId=10),
            lambda data: data["media"]["lanes"][0].update(position=590),
            lambda data: data["media"]["lanes"][0]["video"].update(readyState=0),
            lambda data: data.update(capturedAt=1000000),
        ):
            changed = copy.deepcopy(second)
            mutate(changed)
            self.assertEqual(wb.progress_verdict(row(first), row(changed), (5, 5))[0], "unknown")
        second["media"]["lanes"][0]["position"] = 10
        self.assertEqual(wb.progress_verdict(row(first), row(second), (5, 5)), ("fail", "media_did_not_progress"))

    def test_media_progress_rejects_invalid_clocks_and_backward_seeks(self):
        def row(data):
            return [{"lane": "web", "status": "observed", "data": data}]
        for mutate in (
            lambda first, last: first.update(capturedAt=0),
            lambda first, last: first.update(capturedAt=None),
            lambda first, last: first.update(capturedAt=-1),
            lambda first, last: first.update(collectionMs=None),
            lambda first, last: last.update(collectionMs=None),
            lambda first, last: first["reasons"].append("invalid_sample"),
            lambda first, last: last["reasons"].append("invalid_sample"),
            lambda first, last: last.update(capturedAt=1000000 + 3605000),
            lambda first, last: last["media"]["lanes"][0].update(position=9),
        ):
            first, last = snapshot(), snapshot(15, 1005000)
            mutate(first, last)
            self.assertEqual(wb.progress_verdict(row(first), row(last), (5, 5))[0], "unknown")
        for interval in (None, (0, 0), (float("nan"), 5), (5, float("inf")), (5, 4), (-1, 5), (1, 301)):
            self.assertEqual(wb.progress_verdict(row(snapshot()), row(snapshot(15, 1005000)), interval)[0], "unknown")

    def test_media_progress_runner_uses_host_monotonic_interval(self):
        for first_capture in (0, 1700000000000):
            self.client.snapshots = [snapshot(12.5, first_capture), snapshot(80, 1700003605000)]
            with tempfile.TemporaryDirectory() as temp:
                code, result, _ = self.execute(["test", "run", "media-progress", "--lane", "web",
                    "--duration", "5", "--report", str(Path(temp).resolve() / "case"), "--json"])
            self.assertEqual(code, 3)
            self.assertEqual(result["verdict"], "unknown")
            self.assertEqual(result["sample_interval_seconds"], {"min": 5, "max": 5})

    def test_media_progress_allows_clock_quantization_within_collection_windows(self):
        def row(data):
            return [{"lane": "web", "status": "observed", "data": data}]
        self.assertEqual(wb.progress_verdict(row(snapshot()), row(snapshot(15, 1005000)), (4.5, 6)),
                         ("pass", "same_media_progress_observed"))

    def test_read_only_scenario_writes_verifiable_private_report(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "case"
            code, result, text = self.execute(["test", "run", "media-progress", "--lane", "web",
                                               "--duration", "5", "--report", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(result["verdict"], "pass")
            self.assertEqual(result["evidence_level"], "decoder_progress_only")
            manifest = json.loads((path / "manifest.json").read_text())
            raw = (path / "result.json").read_bytes()
            self.assertEqual(manifest["files"], [{"name": "result.json", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}])
            self.assertEqual(len(result["tooling"]["cli_sha256"]), 64)
            self.assertNotIn(SECRET, text)
            self.assertFalse(list(path.glob("*.partial")))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
                self.assertEqual((path / "result.json").stat().st_mode & 0o777, 0o600)

    def test_native_clock_progress_never_becomes_a_passing_media_test(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "case"
            code, result, _ = self.execute(["test", "run", "media-progress", "--lane", "native", "--report", str(path), "-j"])
            self.assertEqual(code, 3)
            self.assertEqual(result["reason"], "media_identity_unavailable")

    def test_native_progress_requires_position_not_system_or_decoder_counters(self):
        before = [native_observation(native_playing())]
        after = [native_observation(native_playing(15, 1005000, 65))]
        self.assertEqual(wb.progress_verdict(before, after, (5, 5)),
                         ("pass", "same_native_media_progress_observed"))
        after[0]["data"]["player"]["video"]["position"] = 10
        after[0]["data"]["player"]["decoder"]["decoded_frames"] = 1000
        self.assertEqual(wb.progress_verdict(before, after, (5, 5)),
                         ("fail", "media_did_not_progress"))
        for position in (9, 80):
            after[0]["data"]["player"]["video"]["position"] = position
            self.assertEqual(wb.progress_verdict(before, after, (5, 5)),
                             ("unknown", "position_discontinuity"))

    def test_native_progress_rejects_partial_and_restarted_identity(self):
        before = [native_observation(native_playing())]
        complete = native_observation(native_playing(15, 1005000, 65))
        for path in (("runtime",), ("boot_id",), ("app_pid",), ("uptime_seconds",),
                     ("system_evidence",), ("system_evidence", "app_pid"),
                     ("system_evidence", "captured_uptime_seconds"),
                     ("player", "identity"), ("player", "identity", "web_runtime"),
                     ("player", "identity", "generation"), ("player", "identity", "handle_id"),
                     ("player", "identity", "captured_at"), ("player", "decoder"),
                     ("player", "video", "position")):
            for missing in (False, True):
                row = copy.deepcopy(complete)
                parent = row["data"]
                for key in path[:-1]: parent = parent[key]
                if missing: parent.pop(path[-1])
                else: parent[path[-1]] = None
                with self.subTest(path=path, missing=missing):
                    self.assertEqual(wb.progress_verdict(before, [row], (5, 5))[0], "unknown")
        for mutate in (
            lambda d: d.update(runtime="new-agent"),
            lambda d: d.update(boot_id="abcdef12-1234-1234-1234-123456789abc"),
            lambda d: d.update(app_pid=456),
            lambda d: d["system_evidence"].update(app_pid=456),
            lambda d: d["player"]["identity"].update(web_runtime="new-webview"),
            lambda d: d["player"]["identity"].update(generation=8),
            lambda d: d["player"]["identity"].update(handle_id=10),
            lambda d: d["player"]["identity"].update(kind="live"),
            lambda d: d["player"]["identity"].update(available=False),
            lambda d: d["player"]["identity"].update(consistent=False),
            lambda d: d.update(media_identity_available=False),
            lambda d: d.update(system_evidence={"surface": {"state": "unavailable", "reason": "process_changed"}}),
        ):
            row = copy.deepcopy(complete)
            mutate(row["data"])
            self.assertEqual(wb.progress_verdict(before, [row], (5, 5))[0], "unknown")

    def test_native_progress_rejects_stale_clocks_and_partial_decoder(self):
        before = [native_observation(native_playing())]
        complete = native_observation(native_playing(15, 1005000, 65))
        for mutate in (
            lambda d: d["player"]["identity"].update(captured_at=0),
            lambda d: d["player"]["identity"].update(captured_at=1000000),
            lambda d: d["player"]["identity"].update(captured_at=999000),
            lambda d: d["player"]["identity"].update(captured_at=4605000),
            lambda d: d.update(uptime_seconds=0),
            lambda d: d.update(uptime_seconds=60),
            lambda d: d.update(uptime_seconds=3665),
            lambda d: d["system_evidence"].update(captured_uptime_seconds=64),
            lambda d: d["system_evidence"].update(captured_uptime_seconds=3665),
            lambda d: d["player"]["video"].update(paused=True),
            lambda d: d["player"]["video"].update(ended=True),
            lambda d: d["player"]["video"].update(ready=1),
            lambda d: d["player"]["video"].update(error=3),
            lambda d: d["player"]["video"].update(width=0),
            lambda d: d["player"]["video"].update(position=True),
            lambda d: d["player"]["video"].update(position=float("nan")),
            lambda d: d["player"].update(ready=False),
            lambda d: d.update(webview_responsive=False),
        ):
            row = copy.deepcopy(complete)
            mutate(row["data"])
            self.assertEqual(wb.progress_verdict(before, [row], (5, 5))[0], "unknown")
        for interval in (None, (0, 0), (float("nan"), 5), (5, float("inf")), (5, 4), (-1, 5), (1, 301)):
            self.assertEqual(wb.progress_verdict(before, [complete], interval)[0], "unknown")
        self.assertEqual(wb.progress_verdict(before, [complete], (4.5, 6))[0], "pass")

    def test_auto_progress_keeps_web_authority_when_native_advances(self):
        before = [native_observation(native_playing())]
        after = [native_observation(native_playing(15, 1005000, 65))]
        unavailable = {"lane": "web", "status": "unknown", "reason": "unavailable"}
        self.assertEqual(wb.progress_verdict([unavailable, *before], [unavailable, *after], (5, 5)),
                         ("unknown", "media_identity_unavailable"))
        web_first = {"lane": "web", "status": "observed", "data": snapshot()}
        web_last = {"lane": "web", "status": "observed", "data": snapshot(10, 1005000)}
        self.assertEqual(wb.progress_verdict([web_first, *before], [web_last, *after], (5, 5)),
                         ("fail", "media_did_not_progress"))

    def test_native_progress_runner_uses_only_health_and_writes_bounded_evidence(self):
        for position, expected in ((15, 0), (10, 2)):
            self.client.calls.clear()
            captured = int(self.clock.value * 1000)
            self.client.native_snapshots = [native_playing(10, captured, 60),
                                           native_playing(position, captured + 5000, 65)]
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp).resolve() / "native-progress"
                code, result, text = self.execute(["test", "run", "media-progress", "--lane", "native",
                    "--duration", "5", "--report", str(path), "-j"])
                self.assertEqual(code, expected)
                self.assertEqual(result["evidence_level"], "decoder_progress_only")
                self.assertEqual(result["evaluator"], "workbench-v2")
                self.assertEqual(result["sample_interval_seconds"], {"min": 5, "max": 5})
                self.assertFalse(result["physical_display_verified"])
                self.assertTrue(result["read_only"])
                self.assertNotIn(SECRET, text)
                saved = (path / "result.json").read_bytes()
                manifest = json.loads((path / "manifest.json").read_text())
                self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(saved).hexdigest())
            self.assertEqual([row[:3] for row in self.client.calls],
                [("native", "maintenance", {"operation": "health"})] * 2)

    def test_health_does_not_hide_an_unresponsive_webview(self):
        self.client.native["webview_responsive"] = False
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "case"
            code, result, _ = self.execute(["test", "run", "health", "--report", str(path), "-j"])
            self.assertEqual(code, 2)
            self.assertEqual(result["verdict"], "fail")

    def test_bundle_removes_free_text_and_labels_log_correlation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "case"
            code, result, text = self.execute(["bundle", "--out", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertNotIn(SECRET, text)
            logs = result["observations"][1]["logs"]
            self.assertEqual(logs["events"], [{"time": 1, "event": "poll_failed"}])
            self.assertEqual(logs["unknown_events_omitted"], 1)
            self.assertEqual(logs["runtime_correlation"], "unavailable")

    def test_malformed_native_logs_cannot_crash_or_create_false_event_evidence(self):
        for entry in (None, {}, {"event": "poll_failed"}, {"event": "poll_failed", "time": None},
                      {"event": "poll_failed", "time": True}, {"event": None, "time": 1}):
            with self.assertRaises(wb.InvalidData):
                wb.native_metadata(ott, {"version": 1, "events": [entry]}, "logs")

    def test_existing_directory_and_symlink_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve()
            with self.assertRaises(wb.ObservationError):
                wb.write_report(path, {})
            if os.name == "posix":
                (path / "link").symlink_to(path, target_is_directory=True)
                with self.assertRaises(OSError):
                    wb.write_report(path / "link" / "escape", {})
                self.assertFalse((path / "escape").exists())

    def test_native_operation_uses_durable_history_without_replaying(self):
        key = "b" * 32
        self.client.native["operations"] = [{"request_id": key, "action": "lifecycle", "operation": "reload_player",
            "state": "handler_completed", "runtime": "android-old", "updated_at": 123, "evidence": "handler_completed"}]
        code, result, _ = self.execute(["operation", key, "--lane", "native", "-j"])
        self.assertEqual(code, 0)
        self.assertEqual(result["operation_state"], "handler_completed")
        self.assertEqual(result["observations"][0]["data"]["receipt"]["runtime"], "android-old")
        self.assertFalse(result["observations"][0]["data"]["effect_observed"])
        self.assertEqual([row[:3] for row in self.client.calls], [("native", "maintenance", {"operation": "health"})])

    def test_native_operation_entrypoint_rejects_incomplete_receipt(self):
        key = "c" * 32
        complete = {"request_id": key, "state": "handler_completed", "action": "lifecycle",
                    "operation": "reload_player", "runtime": "old", "updated_at": 123, "evidence": "handler_completed"}
        for missing in (None, "action", "operation", "runtime", "updated_at"):
            self.client.calls = []
            row = copy.deepcopy(complete)
            if missing: row.pop(missing)
            self.client.native["operations"] = [row]
            out = io.StringIO()
            with mock.patch.object(ott, "Client", return_value=self.client), mock.patch.object(ott, "read_json", return_value={}), contextlib.redirect_stdout(out):
                code = ott.main(["-t", "20", "a1", "operation", key, "--lane", "native", "--json"])
            result = json.loads(out.getvalue())
            self.assertEqual([call[:3] for call in self.client.calls], [("native", "maintenance", {"operation": "health"})])
            self.assertEqual(code, 3 if missing else 0)
            if missing:
                self.assertEqual(result["observations"][0]["reason"], "invalid_response")
                self.assertNotIn("operation_state", result)
                self.assertNotIn("handler_completed", out.getvalue())
            else:
                self.assertEqual(result["operation_state"], "handler_completed")
                self.assertEqual(result["observations"][0]["data"]["receipt"], complete)
                self.assertFalse(result["observations"][0]["data"]["effect_observed"])

    def test_invalid_arguments_are_json_and_issue_no_requests(self):
        for words in (["test", "run", "health", "-j"], ["test", "run", "shell", "-j"],
                      ["inspect", "--view", "private", "-j"], ["doctor", "--eval", SECRET, "-j"],
                      ["operation", "A" * 32, "-j"], ["operation", "a" * 32, "--lane", "auto", "-j"],
                      ["test", "run", "media-progress", "--duration", "NaN", "--report", "unused", "-j"]):
            code, result, text = self.execute(words)
            self.assertEqual(code, 1)
            self.assertEqual(result["reason"], "invalid_arguments")
            self.assertNotIn(SECRET, text)
        self.assertEqual(self.client.calls, [])

    def test_dispatch_accepts_json_before_or_after_command(self):
        for words in (["-j", "tv", "test", "list"], ["tv", "test", "list", "-j"],
                      ["tv", "TEST", "list", "--json"]):
            out = io.StringIO()
            with mock.patch.object(ott, "Client", return_value=self.client), mock.patch.object(ott, "read_json", return_value={}), \
                    contextlib.redirect_stdout(out):
                code = ott.main(words)
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out.getvalue())["scenarios"]), 2)
        self.assertEqual(self.client.calls, [])

    def test_source_cli_copy_contains_loadable_workbench(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            shutil.copytree(ROOT / "cli", directory / "cli", ignore=shutil.ignore_patterns("__pycache__"))
            (directory / "credentials.json").write_text(json.dumps({"admin_token": "synthetic", "devices": [{"id": "web"}]}))
            (directory / "config.json").write_text(json.dumps({"server": "http://127.0.0.1:9", "server_config": str(directory / "credentials.json"), "players": {"tv": "web"}}))
            result = subprocess.run([sys.executable, str(directory / "cli/ott.py"), "-c", str(directory / "config.json"),
                                     "tv", "test", "list", "--json"], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["scenarios"]), 2)

    def test_real_http_client_collects_snapshot_without_exporting_credentials(self):
        calls, results = [], {}
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, self.headers.get("Authorization"), request))
                action = request["action"]
                request_id = format(len(calls), "032x")
                if action == "capabilities":
                    data = capabilities()
                elif request == {"action": "inspect", "params": {"version": 1, "runtime": RUNTIME, "section": "snapshot"}}:
                    data = {"version": 1, "runtime": RUNTIME, "section": "snapshot", "data": snapshot()}
                    data["data"]["private"] = SECRET
                else:
                    self.reply(400, {})
                    return
                results[request_id] = data
                self.reply(202, {"id": request_id})

            def do_GET(self):
                query = parse_qs(urlsplit(self.path).query)
                self.reply(200, {"id": query["id"][0], "status": "ok", "data": results[query["id"][0]]})

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                credentials = Path(temp) / "credentials.json"
                credentials.write_text(json.dumps({"admin_token": SECRET, "devices": [{"id": "web"}]}))
                client = ott.Client({"server": f"http://127.0.0.1:{server.server_port}/prefix", "server_config": str(credentials)}, 5)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = wb.run(ott, client, "web", ["inspect", "--lane", "web", "--json"], 5)
                self.assertEqual(code, 0, out.getvalue())
                self.assertNotIn(SECRET, out.getvalue())
                result = json.loads(out.getvalue())
                self.assertEqual(result["observations"][0]["data"], snapshot())
                self.assertEqual([row["request_id"] for row in result["observations"][0]["requests"]], ["0" * 31 + "1", "0" * 31 + "2"])
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(path == "/prefix/api/requests?device_id=web" and auth == "Bearer " + SECRET for path, auth, _ in calls))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(1)


class ReceiptTest(unittest.TestCase):
    def client(self):
        value = object.__new__(ott.Client)
        value.timeout = 2
        return value

    def test_receipt_retains_id_and_legacy_call_retains_only_data(self):
        for receipt in (True, False):
            client = self.client()
            client.api = mock.Mock(side_effect=[(202, {"id": "a" * 32}), (200, {"id": "a" * 32, "status": "ok", "data": {"value": 1}})])
            with mock.patch.object(ott.time, "sleep"):
                result = (client.call_receipt if receipt else client.call)("web", "status", {})
            self.assertEqual(result, {"request_id": "a" * 32, "status": "ok", "data": {"value": 1}} if receipt else {"value": 1})
            self.assertEqual(sum(call.args[1:] != () for call in client.api.call_args_list), 1)

    def test_lost_post_is_not_retried_and_known_id_survives_read_failure(self):
        client = self.client()
        client.api = mock.Mock(side_effect=ott.TransportError("offline"))
        with self.assertRaises(ott.Error):
            client.call_receipt("web", "status", {})
        self.assertEqual(client.api.call_count, 1)
        client.api = mock.Mock(side_effect=[(202, {"id": "a" * 32}), ott.HTTPError(404)])
        with mock.patch.object(ott.time, "sleep"), self.assertRaises(ott.Error) as caught:
            client.call_receipt("web", "status", {})
        self.assertEqual(caught.exception.request_id, "a" * 32)
        self.assertEqual(client.api.call_count, 2)

    def test_crossed_or_missing_receipt_id_is_rejected_for_every_terminal_status(self):
        for status in ("ok", "rejected", "unsupported"):
            for response_id in ("b" * 32, None):
                client = self.client()
                result = {"status": status, "data": {"private": SECRET}}
                if response_id is not None:
                    result["id"] = response_id
                client.api = mock.Mock(side_effect=[(202, {"id": "a" * 32}), (200, result)])
                with mock.patch.object(ott.time, "sleep"), self.assertRaises(ott.Error) as caught:
                    client.call_receipt("web", "status", {})
                self.assertEqual(caught.exception.request_id, "a" * 32)
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertEqual(client.api.call_count, 2)

    def test_unsupported_receipt_retains_envelope_for_caller_validation(self):
        client = self.client()
        data = {"version": 1, "runtime": RUNTIME, "section": "doctor", "error": "unsupported"}
        client.api = mock.Mock(side_effect=[(202, {"id": "a" * 32}),
                                           (200, {"id": "a" * 32, "status": "unsupported", "data": data})])
        with mock.patch.object(ott.time, "sleep"), self.assertRaises(ott.PlayerUnsupported) as caught:
            client.call_receipt("web", "inspect", {"version": 1, "runtime": RUNTIME, "section": "doctor"})
        self.assertEqual(caught.exception.data, data)
        self.assertEqual(caught.exception.request_id, "a" * 32)
        self.assertIsNone(ott.PlayerUnsupported("legacy constructor").data)

    def test_receipt_hook_emits_each_terminal_status_without_private_data(self):
        for status in ("ok", "rejected", "unsupported"):
            client = self.client()
            client.on_receipt = ott.print_request_receipt
            client.api = mock.Mock(side_effect=[(202, {"id": "a" * 32}),
                (200, {"id": "a" * 32, "status": status, "data": {"error": SECRET}})])
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(ott.time, "sleep"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                if status == "ok":
                    client.call_receipt("private-device", "restart", {"private": SECRET})
                else:
                    with self.assertRaises(ott.Error):
                        client.call_receipt("private-device", "restart", {"private": SECRET})
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue().count("\n"), 1)
            self.assertEqual(json.loads(err.getvalue()), {"action": "restart", "request_id": "a" * 32, "status": status})
            self.assertNotIn(SECRET, err.getvalue())
            self.assertNotIn("private-device", err.getvalue())

    def test_receipt_hook_preserves_unknown_outcomes_without_repeating_post(self):
        cases = [([ott.TransportError(SECRET)], None, 1),
                 ([(202, {"id": SECRET})], None, 1),
                 ([(202, {"id": "a" * 32}), ott.HTTPError(404)], "a" * 32, 2)]
        for responses, expected_id, calls in cases:
            client = self.client()
            receipts = []
            client.on_receipt = receipts.append
            client.api = mock.Mock(side_effect=responses)
            with mock.patch.object(ott.time, "sleep"), self.assertRaises(ott.Error):
                client.call_receipt("web", "restart", {"private": SECRET})
            self.assertEqual(receipts, [{"action": "restart", "request_id": expected_id, "status": "unknown"}])
            self.assertEqual(client.api.call_count, calls)
            self.assertEqual(sum(bool(call.args[1:]) for call in client.api.call_args_list), 1)

    def test_each_call_emits_one_receipt_and_hook_failure_never_replays(self):
        client = self.client()
        receipts = []
        client.on_receipt = receipts.append
        client.api = mock.Mock(side_effect=[
            (202, {"id": "a" * 32}), (200, {"id": "a" * 32, "status": "ok", "data": {}}),
            (202, {"id": "b" * 32}), (200, {"id": "b" * 32, "status": "ok", "data": {}})])
        with mock.patch.object(ott.time, "sleep"):
            client.call("web", "capabilities", {})
            client.call("web", "restart", {"target": "player"})
        self.assertEqual(receipts, [{"action": "capabilities", "request_id": "a" * 32, "status": "ok"},
                                    {"action": "restart", "request_id": "b" * 32, "status": "ok"}])
        client.on_receipt = mock.Mock(side_effect=OSError("closed stderr"))
        client.api = mock.Mock(side_effect=[(202, {"id": "c" * 32}), (200, {"id": "c" * 32, "status": "ok", "data": {}})])
        with mock.patch.object(ott.time, "sleep"):
            self.assertEqual(client.call("web", "restart", {}), {})
        self.assertEqual(client.api.call_count, 2)
        client.on_receipt.assert_called_once()

    def test_global_receipt_flag_preserves_ordinary_stdout(self):
        stdout = []
        for flag in (False, True):
            client = self.client()
            client.device = lambda _name: "web"
            result = {"target": "player", "accepted": True, "dispatched": False, "effect": "reload-after-ack"}
            client.api = mock.Mock(side_effect=[(202, {"id": "a" * 32}),
                (200, {"id": "a" * 32, "status": "ok", "data": result})])
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(ott.time, "sleep"), mock.patch.object(ott, "Client", return_value=client), \
                    mock.patch.object(ott, "read_json", return_value={}), mock.patch.object(ott, "management", return_value=False), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = ott.main((["--receipt"] if flag else []) + ["a1", "restart"])
            self.assertEqual(code, 0, err.getvalue())
            stdout.append(out.getvalue())
            if flag:
                self.assertEqual(json.loads(err.getvalue()), {"action": "restart", "request_id": "a" * 32, "status": "ok"})
            else:
                self.assertEqual(err.getvalue(), "")
        self.assertEqual(stdout[0], stdout[1])
        self.assertIn("Player reload accepted", stdout[0])


class ResponseJSONTest(unittest.TestCase):
    def test_ambiguous_and_nonfinite_wire_json_is_rejected_without_raw_body(self):
        bodies = [
            b'{"id":"first","id":"second"}',
            b'{"data":{"runtime":"page-one","runtime":"page-other"}}',
            b'{"data":{"position":NaN}}',
            b'{"data":{"position":Infinity}}',
            b'{"data":{"position":-Infinity}}',
            b'{"data":{"position":1e9999}}',
            ('{"private":"' + SECRET + '",').encode(),
        ]
        for body in bodies:
            client = object.__new__(ott.Client)
            client.server, client.token = "https://controller.invalid", "synthetic"
            client.opener = mock.Mock()
            response = mock.MagicMock(status=200, length=0)
            response.read1.side_effect = [body, b""]
            client.opener.open.return_value = response
            response.__enter__.return_value = response
            with self.assertRaises(ott.Error) as caught:
                client._api("/api/requests?id=synthetic", None, 10,
                            ott.time.monotonic() + 10, threading.Event())
            self.assertEqual(str(caught.exception), "The server returned invalid JSON")
            self.assertNotIn(SECRET, str(caught.exception))

    def test_finite_json_preserves_existing_scalar_types(self):
        result = ott.decode_response_json(b'{"integer":9007199254740991,"fraction":1.25,"exponent":1e2,"null":null,"bool":true}')
        self.assertEqual(result, {"integer": 9007199254740991, "fraction": 1.25,
                                  "exponent": 100.0, "null": None, "bool": True})
        self.assertIs(type(result["integer"]), int)
        self.assertIs(type(result["bool"]), bool)



class NativeCorrelation(unittest.TestCase):
    def test_shared_identity_is_optional_and_consistent(self):
        raw = native()
        self.assertFalse(wb.native_metadata(ott, raw, "health")["media_identity_available"])
        raw["player"]["identity"] = {"version": 1, "available": True, "web_runtime": "web-one",
            "generation": 3, "handle_id": 9, "kind": "vod", "captured_at": 100, "consistent": True}
        self.assertTrue(wb.native_metadata(ott, raw, "health")["media_identity_available"])
        for key, value in (("handle_id", None), ("consistent", False), ("captured_at", 0), ("version", True)):
            changed = copy.deepcopy(raw);changed["player"]["identity"][key] = value
            with self.assertRaises(wb.InvalidData):wb.native_metadata(ott, changed, "health")

    def test_log_runtime_mismatch_cannot_be_called_correlated(self):
        raw = {"version": 1, "runtime": "native-new", "events": [
            {"time": 1, "event": "poll_failed", "runtime": "native-new", "secret": SECRET}]}
        self.assertEqual(wb.native_metadata(ott, raw, "logs", "native-new")["runtime_correlation"], "matched")
        result = wb.native_metadata(ott, raw, "logs", "native-old")
        self.assertEqual(result["runtime_correlation"], "mismatch")
        self.assertNotIn(SECRET, json.dumps(result))
        raw["events"][0]["runtime"] = "different"
        self.assertNotEqual(wb.native_metadata(ott, raw, "logs", "native-new")["runtime_correlation"], "matched")
        raw["events"][0].pop("runtime")
        self.assertEqual(wb.native_metadata(ott, raw, "logs", "native-new")["runtime_correlation"], "unavailable")
        raw.pop("runtime")
        self.assertEqual(wb.native_metadata(ott, raw, "logs", "native-new")["runtime_correlation"], "unavailable")

if __name__ == "__main__":
    unittest.main()
