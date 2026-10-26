"""Offline bounded performance captures and CAS restoration, without device access."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_debug_cli import DebugClient, V3
from test_workbench_cli import Clock, RUNTIME, SECRET, load, ott

perf = load("performance_test", "performance.py")
ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "contracts/perf-capture-v1.json").read_text())
SID = "a" * 32


class MemoryJournal(perf.Journal):
    """Exercise capture/CAS sequencing without depending on POSIX persistence."""
    def __init__(self, directory):
        self.path = Path(directory).absolute()
        self.fd, self.total, self.files, self.records = None, 0, [], {}

    def write(self, name, value):
        if name in self.records:
            raise FileExistsError(name)
        raw = perf.encoded(value)
        perf.check(self.total + len(raw) <= perf.MAX_EXPORT, "export_size_limit")
        self.records[name] = json.loads(raw)
        self.total += len(raw)
        self.files.append({"name": name, "bytes": len(raw), "sha256": perf.hashlib.sha256(raw).hexdigest()})


class PerfClock(Clock):
    def __init__(self):
        super().__init__()
        self.on_sleep = None

    def time(self):
        return 1700000000 + self.value

    def sleep(self, seconds):
        self.value += seconds
        if self.on_sleep:
            self.on_sleep()


def event(seq=1, elapsed=0):
    return {"seq": seq, "at_ms": elapsed, "kind": "session", "lane": None, "generation": None,
            "handle_id": None, "source_id": None, "data": {"event": "started", "reason": None}}


def session(events=None):
    events = events if events is not None else [event()]
    return {"id": SID, "state": "active", "reason": None, "duration_seconds": 120, "sample_interval_ms": 1000,
            "elapsed_ms": 1000, "remaining_ms": 119000, "started_at_unix_ms": 1700000000000,
            "clock": "performance", "requested": {"raf": False, "long_tasks": True},
            "supported": {"raf": True, "long_tasks": False, "notification_stages": ["received", "shown"],
                          "notification_timings": ["fetch_ms"]},
            "first_seq": events[0]["seq"] if events else 1, "next_seq": events[-1]["seq"] + 1 if events else 1,
            "event_count": len(events), "dropped_events": 0, "rate_dropped": 0, "ring_dropped": 0,
            "oversized_dropped": 0, "bytes": sum(len(perf.encoded(row)) - 1 for row in events)}


def schema_minimal(schema):
    if "$ref" in schema:
        return schema_minimal(CONTRACT["$defs"][schema["$ref"].split("/")[-1]])
    if "anyOf" in schema:
        return schema_minimal(schema["anyOf"][0])
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list) and "null" in kind or kind == "null":
        return None
    if kind == "object":
        return {key: schema_minimal(schema["properties"][key]) for key in schema["required"]}
    if kind == "array":
        return []
    if kind in ("number", "integer"):
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    if kind == "string":
        return SID
    raise AssertionError(schema)


class PerfClient(DebugClient):
    def __init__(self, clock):
        super().__init__(clock)
        self.capabilities.update(copy.deepcopy(V3["capabilities"]))
        self.capabilities.update(CONTRACT["capability"])
        self.capabilities["player_settings"] = {"version": 1, "operations": ["schema", "get", "set"], "max_page_size": 20}
        self.debug = copy.deepcopy(V3["response"]["data"])
        self.current, self.events, self.started = None, [], 0
        self.policy, self.policy_revision, self.policy_stored = 1, 1, False
        self.fail_policy_after_commit = False
        self.fail_page = False
        self.max_page = 64

    def descriptor(self):
        return {"id": "notificationsEnabled", "type": "integer", "scope": "application", "sensitive": False,
                "access": {"read": True, "write": True}, "constraints": {"minimum": 0, "maximum": 1},
                "apply": "immediate", "default_value": 1}

    def policy_data(self, op):
        setting = {"id": "notificationsEnabled", "revision": "policy-" + str(self.policy_revision),
                   "storage": "stored" if self.policy_stored else "default", "value": self.policy,
                   "effective_value": self.policy}
        if self.policy_stored:
            setting["persisted_value"] = self.policy
        data = {"version": 1, "runtime": RUNTIME, "op": op,
                "context": {"token": "source-one", "provider": "m3u", "profile": 1}, "setting": setting}
        if op == "get":
            data["descriptor"] = self.descriptor()
        else:
            data.update(changed=True, persisted=True, application={"state": "completed"})
        return data

    def current_session(self):
        if self.current is None:
            return None
        self.current.update(elapsed_ms=(self.clock.monotonic() - self.started) * 1000,
                            remaining_ms=max(0, self.current["duration_seconds"] * 1000 - (self.clock.monotonic() - self.started) * 1000)
                            if self.current["state"] == "active" else 0)
        self.current.update(first_seq=self.events[0]["seq"] if self.events else 1,
                            next_seq=self.events[-1]["seq"] + 1 if self.events else 1,
                            event_count=len(self.events), bytes=sum(len(perf.encoded(row)) - 1 for row in self.events))
        return copy.deepcopy(self.current)

    def call_receipt(self, target, action, params):
        if action not in ("perf_capture", "player_settings"):
            return super().call_receipt(target, action, params)
        self.calls.append((target, action, copy.deepcopy(params), self.timeout))
        if action == "player_settings":
            if params["op"] == "set":
                if params["revision"] != "policy-" + str(self.policy_revision):
                    raise ott.PlayerRejected("conflict", {"version": 1, "op": "set", "runtime": RUNTIME,
                        "error": "changed", "reason": "revision_conflict"})
                self.policy, self.policy_stored = params["value"], True
                self.policy_revision += 1
                if self.fail_policy_after_commit:
                    raise ott.TransportError("private raw exception " + SECRET)
            data = self.policy_data(params["op"])
        else:
            op = params["op"]
            if op == "start":
                self.current, self.events, self.started = session(), [event()], self.clock.monotonic()
                self.current.update(id=params["session_id"], duration_seconds=params.get("duration_seconds", 120),
                                    requested={"raf": params.get("raf", False), "long_tasks": params.get("long_tasks", True)})
            elif op == "stop":
                self.current.update(state="stopped", reason="requested")
                self.events.append(dict(event(len(self.events) + 1, (self.clock.monotonic() - self.started) * 1000),
                                        data={"event": "stopped", "reason": "requested"}))
            data = {"version": 1, "op": op, "runtime": RUNTIME, "session": self.current_session()}
            if op == "read":
                if self.fail_page:
                    raise ott.TransportError("private " + SECRET)
                candidates = [row for row in self.events if row["seq"] > params["after_seq"]]
                rows = candidates[:min(params.get("limit", 64), self.max_page)]
                more = len(candidates) > len(rows)
                data.update(events=copy.deepcopy(rows), more=more, lost_before=max(0, data["session"]["first_seq"] - params["after_seq"] - 1),
                            next_after_seq=rows[-1]["seq"] if more else data["session"]["next_seq"] - 1)
        return {"request_id": "request-" + str(len(self.calls)), "status": "ok", "data": data}


class PerformanceTest(unittest.TestCase):
    def setUp(self):
        self.clock = PerfClock()
        self.client = PerfClient(self.clock)
        self.journal = None

    def run_perf(self, words, timeout=240, *, memory=True):
        out = io.StringIO()
        def journal(directory):
            self.journal = MemoryJournal(directory)
            return self.journal
        persistence = mock.patch.object(perf, "Journal", side_effect=journal) if memory else contextlib.nullcontext()
        with persistence, mock.patch.object(perf, "time", self.clock), contextlib.redirect_stdout(out):
            status = perf.run(ott, self.client, "web", words, timeout, True)
        self.assertNotIn(SECRET, out.getvalue())
        return status, json.loads(out.getvalue())

    def test_capability_matches_contract_and_is_strict(self):
        self.assertEqual(perf.CAPABILITY, CONTRACT["capability"]["perf_capture"])
        for key in perf.CAPABILITY:
            value = dict(perf.CAPABILITY)
            value[key] = True
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(self.client.capabilities, perf_capture=value))

    def test_default_status_is_read_only_and_missing_capability_never_probes(self):
        code, data = self.run_perf([])
        self.assertEqual(code, 0)
        self.assertIsNone(data["data"]["session"])
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities", "perf_capture"])
        del self.client.capabilities["perf_capture"]
        self.client.calls.clear()
        code, data = self.run_perf(["start"])
        self.assertEqual(data["reason"], "unsupported")
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities"])

    def test_start_defaults_and_explicit_stop(self):
        code, data = self.run_perf(["start", "--session", SID])
        self.assertEqual(code, 0)
        self.assertEqual(data["data"]["session"]["duration_seconds"], 120)
        self.assertEqual(data["data"]["session"]["requested"], {"raf": False, "long_tasks": True})
        code, data = self.run_perf(["stop", SID])
        self.assertEqual(code, 0)
        self.assertEqual(data["data"]["session"]["state"], "stopped")

    def test_trial_preflights_timeout_before_any_request(self):
        code, data = self.run_perf(["trial", "notifications", "--out", "/unreached"], 45)
        self.assertEqual(data["reason"], "insufficient_timeout")
        self.assertGreater(data["required_timeout"], 45)
        self.assertEqual(self.client.calls, [])

    def test_trial_restores_policy_and_reports_changed_default_storage(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root).resolve() / "trial"
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(output)], 180)
            self.assertEqual(code, 0, result)
            self.assertEqual([row["phase"] for row in result["phases"]], ["A1", "B", "A2"])
            self.assertEqual([row["notification_policy"] for row in result["phases"]], [1, 0, 1])
            self.assertEqual(result["phases"][0]["start_session"]["elapsed_ms"], 15000)
            self.assertTrue(all(row["steady_from_ms"] == row["start_session"]["elapsed_ms"] for row in result["phases"]))
            self.assertIs(result["policy_restored"], True)
            self.assertIs(result["storage_representation_changed"], True)
            changes = [row[2] for row in self.client.calls if row[1] == "player_settings" and row[2]["op"] == "set"]
            self.assertEqual([row["value"] for row in changes], [0, 1])
            self.assertEqual([row["revision"] for row in changes], ["policy-1", "policy-2"])
            self.assertIn("trial-plan.json", self.journal.records)
            self.assertIn("manifest.json", self.journal.records)

    def test_external_same_value_aba_prevents_restore(self):
        def external():
            if self.client.policy == 0:
                self.client.policy_revision += 2
                self.clock.on_sleep = None
        self.clock.on_sleep = external
        with tempfile.TemporaryDirectory() as root:
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(Path(root).resolve() / "trial")], 180)
        self.assertNotEqual(code, 0)
        self.assertEqual(result["reason"], "revision_conflict")
        self.assertIsNot(result["policy_restored"], True)
        self.assertEqual(self.client.policy, 0)
        changes = [row for row in self.client.calls if row[1] == "player_settings" and row[2]["op"] == "set"]
        self.assertEqual(len(changes), 2, "one B write and one rejected CAS, no retry")

    def test_interrupt_restores_only_known_own_revision_then_stops(self):
        def interrupt():
            if self.client.policy == 0:
                self.clock.on_sleep = None
                raise KeyboardInterrupt()
        self.clock.on_sleep = interrupt
        with tempfile.TemporaryDirectory() as root:
            output = Path(root).resolve() / "trial"
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(output)], 180)
            self.assertIn("manifest.json", self.journal.records)
        self.assertEqual(code, 130)
        self.assertIs(result["policy_restored"], True)
        self.assertEqual(self.client.policy, 1)
        self.assertEqual(self.client.current["state"], "stopped")

    def test_unknown_committed_policy_write_never_blindly_restores(self):
        self.client.fail_policy_after_commit = True
        with tempfile.TemporaryDirectory() as root:
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(Path(root).resolve() / "trial")], 180)
        self.assertNotEqual(code, 0)
        self.assertIs(result["unknown_outcome"], True)
        self.assertIsNot(result["policy_restored"], True)
        self.assertEqual(self.client.policy, 0)
        self.assertEqual(len([row for row in self.client.calls if row[1] == "player_settings" and row[2]["op"] == "set"]), 1)

    def test_export_page_limit_is_partial_and_never_tails_forever(self):
        self.run_perf(["start", "--session", SID])
        self.client.events = [event(i) for i in range(1, 101)]
        self.client.max_page = 1
        with tempfile.TemporaryDirectory() as root:
            code, result = self.run_perf(["export", SID, "--out", str(Path(root).resolve() / "export")])
        self.assertEqual(code, 3)
        self.assertEqual(result["capture"]["pages"], 32)
        self.assertEqual(len(result["capture"]["events"]), 32)
        self.assertIn("page_limit", result["capture"]["partial_reasons"])

    def test_export_failure_keeps_safe_partial_evidence(self):
        self.run_perf(["start", "--session", SID])
        self.client.fail_page = True
        with tempfile.TemporaryDirectory() as root:
            output = Path(root).resolve() / "export"
            code, result = self.run_perf(["export", SID, "--out", str(output)])
            self.assertIn("manifest.json", self.journal.records)
        self.assertEqual(code, 3)
        self.assertFalse(result["capture"]["complete"])
        self.assertEqual(len([row for row in self.client.calls if row[1] == "perf_capture" and row[2]["op"] == "read"]), 1)

    def test_result_binding_and_invalid_events_fail_closed(self):
        request = {"version": 1, "runtime": RUNTIME, "op": "read", "session_id": SID, "after_seq": 0, "limit": 64}
        raw = {"version": 1, "runtime": RUNTIME, "op": "read", "session": session(), "events": [event()],
               "next_after_seq": 1, "more": False, "lost_before": 0}
        self.assertEqual(perf.metadata(raw, request), raw)
        mutations = [lambda d: d.update(runtime="other"), lambda d: d.update(more=True, events=[]),
                     lambda d: d["session"].update(id="b" * 32), lambda d: d["events"][0].update(at_ms=2000),
                     lambda d: d["events"][0].update(url=SECRET), lambda d: d.update(next_after_seq=0),
                     lambda d: d["session"]["supported"].update(notification_stages=["shown", "shown"]),
                     lambda d: d.update(more=True), lambda d: d["session"].update(event_count=0),
                     lambda d: d.update(events=[])]
        for change in mutations:
            modified = copy.deepcopy(raw)
            change(modified)
            with self.assertRaises(perf.Failure):
                perf.metadata(modified, request)

    def test_every_shared_event_shape_and_frame_null_semantics(self):
        context = session()
        context["supported"].update(notification_stages=list(perf.STAGES), notification_timings=list(perf.TIMINGS))
        for kind in perf.EVENT_DATA:
            row = schema_minimal(CONTRACT["$defs"][kind + "_event"])
            perf.validate_event(row, context)
        row = schema_minimal(CONTRACT["$defs"]["sample_event"])
        row["data"]["frames"].update(source="unsupported", total=0)
        with self.assertRaises(perf.Failure):
            perf.validate_event(row, context)
        row["data"]["frames"].update(source="quality", total=0, dropped=0)
        perf.validate_event(row, context)
        row["data"]["buffered"] = {"range_count": 4, "truncated_count": 2,
             "ranges": [{"index": 0, "start_seconds": 0, "end_seconds": 1},
                        {"index": 2, "start_seconds": 3, "end_seconds": 4}]}
        with self.assertRaises(perf.Failure):
            perf.validate_event(row, context)

    def test_help_does_not_load_credentials(self):
        for tail in (["--help"], ["trial", "--help"], ["export", "--help"]):
            with mock.patch.object(ott, "read_json", side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                ott.main(["f", "perf", *tail])
            self.assertEqual(stopped.exception.code, 0)

    @unittest.skipUnless(os.name == "posix", "Real private directory persistence requires POSIX")
    def test_existing_output_prevents_any_request(self):
        with tempfile.TemporaryDirectory() as root:
            code, result = self.run_perf(["trial", "notifications", "--out", str(Path(root).resolve())], memory=False)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.client.calls, [])

    def test_request_is_saved_before_policy_mutation(self):
        original = self.client.call_receipt
        with tempfile.TemporaryDirectory() as root:
            output = Path(root).resolve() / "trial"
            def verified(target, action, params):
                if action == "player_settings" and params["op"] == "set":
                    saved = [value for name, value in self.journal.records.items() if name.startswith("request-")]
                    self.assertTrue(any(row["action"] == action and row["params"] == params for row in saved))
                    self.assertIn("trial-plan.json", self.journal.records)
                return original(target, action, params)
            self.client.call_receipt = verified
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(output)], 180)
        self.assertEqual(code, 0, result)

    @unittest.skipUnless(os.name == "posix", "Real private directory persistence requires POSIX")
    def test_posix_trial_persists_request_before_write_with_private_permissions(self):
        original = self.client.call_receipt
        with tempfile.TemporaryDirectory() as root:
            output = Path(root).resolve() / "trial"
            def verified(target, action, params):
                if action == "player_settings" and params["op"] == "set":
                    saved = [json.loads(p.read_text()) for p in output.glob("request-*.json")]
                    self.assertTrue(any(row["action"] == action and row["params"] == params for row in saved))
                    self.assertTrue((output / "trial-plan.json").exists())
                return original(target, action, params)
            self.client.call_receipt = verified
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(output)], 180, memory=False)
            self.assertEqual(code, 0, result)
            self.assertTrue(result["policy_restored"])
            self.assertTrue((output / "manifest.json").exists())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all((p.stat().st_mode & 0o777) == 0o600 for p in output.iterdir()))
            saved_result = json.loads((output / "result.json").read_text())
            self.assertTrue(saved_result["policy_restored"])

    def test_non_posix_export_and_trial_refuse_before_any_request(self):
        with tempfile.TemporaryDirectory() as root:
            for operation in (["export", SID], ["trial", "notifications"]):
                output = Path(root).resolve() / operation[0]
                with self.subTest(operation=operation[0]), mock.patch.object(perf, "os", SimpleNamespace(name="nt")):
                    code, result = self.run_perf([*operation, "--out", str(output)], memory=False)
                self.assertEqual(code, 3)
                self.assertEqual(result["reason"], "private_output_requires_posix")
                self.assertFalse(result["unknown_outcome"])
                self.assertEqual(self.client.calls, [])
                self.assertFalse(output.exists())

    def test_memory_trial_keeps_algorithm_coverage_without_posix(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(perf, "os", SimpleNamespace(name="nt")):
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(Path(root).resolve() / "trial")], 180)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["policy_restored"])
        self.assertEqual([row["notification_policy"] for row in result["phases"]], [1, 0, 1])

    def test_zero_frames_and_missing_identity_never_claim_smoothness(self):
        first = schema_minimal(CONTRACT["$defs"]["sample_event"])
        first.update(lane="main", source_id="s1", generation=1, handle_id=1)
        first["data"].update(position_seconds=10, playback_rate=1, paused=False, seeking=False, ended=False)
        first["data"]["frames"].update(source="quality", total=0, dropped=0)
        second = copy.deepcopy(first)
        second.update(seq=2, at_ms=1000)
        notes = set()
        measured = perf.frame_deltas([first, second], notes)
        self.assertIsNone(measured["dropped_frame_ratio"])
        self.assertEqual(measured["stationary_playback_intervals"], 1)
        second["handle_id"] = None
        measured = perf.frame_deltas([first, second], notes)
        self.assertIsNone(measured["total_frame_delta"])
        self.assertIsNone(measured["stationary_playback_intervals"])
        self.assertIn("sample_identity_unavailable", notes)

    def test_real_player_corpus_validates_all_eight_event_families(self):
        corpus = json.loads((ROOT / "tests/fixtures/perf_capture_producer_v1.json").read_text())
        kinds = set()
        self.assertEqual(len(corpus), 3)
        for row in corpus:
            response = row["response"]
            self.assertEqual(response["status"], "ok")
            result = perf.metadata(response["data"], row["params"])
            kinds.update(event["kind"] for event in result.get("events", []))
        self.assertEqual(kinds, set(perf.EVENT_DATA))

    def test_late_read_is_confirmed_but_not_reported_as_timely_success(self):
        self.run_perf(["start", "--session", SID])
        original = self.client.call_receipt
        def delayed(target, action, params):
            response = original(target, action, params)
            if action == "perf_capture" and params["op"] == "read":
                self.clock.value += 11
            return response
        self.client.call_receipt = delayed
        code, result = self.run_perf(["read", SID], 10)
        self.assertEqual(code, 3)
        self.assertEqual(result["reason"], "deadline_exceeded")
        self.assertFalse(result["unknown_outcome"])
        self.assertEqual(result["confirmed_late_result"]["op"], "read")
        self.assertEqual(result["requests"][-1]["state"], "confirmed_late")

    def test_late_confirmed_policy_write_uses_reserved_cleanup_only(self):
        original = self.client.call_receipt
        began = self.clock.value
        def delayed(target, action, params):
            response = original(target, action, params)
            if action == "player_settings" and params["op"] == "set" and params["value"] == 0:
                self.clock.value = began + 166  # Past the work deadline, inside the final 15-second reserve.
            return response
        self.client.call_receipt = delayed
        with tempfile.TemporaryDirectory() as root:
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(Path(root).resolve() / "trial")], 180)
        self.assertEqual(code, 3)
        self.assertEqual(result["reason"], "deadline_exceeded")
        self.assertFalse(result["unknown_outcome"])
        self.assertTrue(result["policy_restored"])
        self.assertEqual(self.client.policy, 1)
        writes = [row[2] for row in self.client.calls if row[1] == "player_settings" and row[2]["op"] == "set"]
        self.assertEqual([row["revision"] for row in writes], ["policy-1", "policy-2"])
        self.assertEqual([row["value"] for row in writes], [0, 1])

    def test_late_policy_write_past_overall_deadline_does_not_send_restore(self):
        original = self.client.call_receipt
        began = self.clock.value
        def delayed(target, action, params):
            response = original(target, action, params)
            if action == "player_settings" and params["op"] == "set":
                self.clock.value = began + 181
            return response
        self.client.call_receipt = delayed
        with tempfile.TemporaryDirectory() as root:
            code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", str(Path(root).resolve() / "trial")], 180)
        self.assertEqual(code, 3)
        self.assertFalse(result["unknown_outcome"])
        self.assertIsNone(result["policy_restored"])
        self.assertEqual(result["last_confirmed_policy"]["setting"]["value"], 0)
        self.assertEqual(self.client.policy, 0)
        self.assertEqual(len([row for row in self.client.calls if row[1] == "player_settings" and row[2]["op"] == "set"]), 1)

    def test_summary_excludes_transition_and_seek_crossing_but_keeps_raw_events(self):
        first = schema_minimal(CONTRACT["$defs"]["sample_event"])
        first.update(seq=2, at_ms=16000, lane="main", generation=1, handle_id=1, source_id="s1")
        first["data"].update(position_seconds=10, playback_rate=1, paused=False, seeking=False, ended=False)
        first["data"]["frames"].update(source="quality", total=10, dropped=0)
        last = copy.deepcopy(first)
        last.update(seq=6, at_ms=21000)
        last["data"]["frames"]["total"] = 110
        last["data"]["position_seconds"] = 20
        loop = schema_minimal(CONTRACT["$defs"]["loop_event"])
        loop.update(seq=1, at_ms=1000)
        during = copy.deepcopy(loop)
        during.update(seq=4, at_ms=19000)
        seeking = schema_minimal(CONTRACT["$defs"]["media_event"])
        seeking.update(seq=3, at_ms=18000, lane="main")
        seeking["data"]["event"] = "seeking"
        seeked = copy.deepcopy(seeking)
        seeked.update(seq=5, at_ms=20000)
        seeked["data"]["event"] = "seeked"
        events = [loop, first, seeking, during, seeked, last]
        result = {"capture": {"events": events, "partial_reasons": [], "last_session": {"elapsed_ms": 30000}},
                  "phases": [{"phase": "A1", "notification_policy": 1, "start_session": {"elapsed_ms": 0},
                              "end_session": {"elapsed_ms": 30000}, "steady_from_ms": 15000,
                              "context": {"build": {}, "engine_observations": [],
                                          "ui": {"documentVisibility": "visible", "documentFocused": True}}}]}
        perf.summarize_trial(result)
        summary = result["analysis"]["phases"][0]
        self.assertEqual(len(result["capture"]["events"]), 6)
        self.assertEqual(summary["excluded_transition_ms"], 15000)
        self.assertEqual(summary["excluded_events"], 4)
        self.assertEqual(summary["events"], 2)
        self.assertEqual(summary["loop_delay_ms"]["samples"], 0)
        self.assertIsNone(summary["total_frame_delta"])
        self.assertIn("phase_transition_excluded", result["analysis"]["confounders"])
        self.assertIn("seek_crossing_interval", result["analysis"]["confounders"])


if __name__ == "__main__":
    unittest.main()
