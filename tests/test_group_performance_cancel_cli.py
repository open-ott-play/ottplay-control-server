"""A cancelled group retains only bounded, owned performance-trial cleanup."""
import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_performance_cli import MemoryJournal, PerfClient, PerfClock, perf
from test_workbench_cli import RUNTIME, SECRET, ott


class TrialTransport(ott.Client):
    """Exercise Client.api and its cancellation checks with an in-memory wire."""
    def __init__(self, clock, mode):
        self.clock, self.mode = clock, mode
        self.backend = PerfClient(clock)
        self.config = {"players": {"t1": "web"}}
        self.credentials = {"devices": [{"id": "web"}]}
        self.server, self.token = "https://controller.invalid", SECRET
        self.timeout, self.on_receipt, self.opener = 180, None, mock.Mock()
        self.clones, self.wire = [], []
        self.waiting = threading.Event()
        self.interrupted = threading.Event()
        if mode == "uncertain":
            self.backend.fail_policy_after_commit = True

    def for_group(self, cancelled):
        child = super().for_group(cancelled)
        self.clones.append(child)
        if not hasattr(self, "group_cancelled"):
            original_wait = cancelled.wait

            def wait(seconds):
                if self.backend.policy == 0 and self.mode != "normal":
                    if self.mode == "stale":
                        self.backend.policy_revision += 2
                    elif self.mode == "near_deadline":
                        self.clock.value = 1178
                    elif self.mode == "expired":
                        self.clock.value = 1181
                    elif self.mode == "late_cancel":
                        raise perf.Failure("trial_observation_failed")
                    self.waiting.set()
                    if not original_wait(5):
                        raise AssertionError("group cancellation did not wake the trial")
                    return True
                self.clock.sleep(seconds)
                return cancelled.is_set()

            cancelled.wait = wait
        elif self.mode == "late_cancel":
            # Cancellation arrives after finally begins, when it creates its
            # limited cleanup authority. The original client must stay blocked.
            self.group_cancelled.set()
        return child

    def call_receipt(self, device, action, params, idempotency_key=None):
        try:
            return self.api("/fixture", {"device": device, "action": action, "params": params})
        except ott.TransportError:
            if self.mode == "uncertain":
                self.group_cancelled.set()
            raise

    def _api(self, path, payload, budget, deadline, cancelled, *, return_raw=False):
        self.wire.append((self, copy.deepcopy(payload), budget))
        self.backend.timeout = budget
        return self.backend.call_receipt(payload["device"], payload["action"], payload["params"])


class GroupPerformanceCancellationTest(unittest.TestCase):
    def run_trial(self, mode="known"):
        clock = PerfClock()
        client = TrialTransport(clock, mode)
        output, errors = io.StringIO(), io.StringIO()
        original_wait = ott.wait

        def interrupt_wait(*args, **kwargs):
            if mode not in ("uncertain", "late_cancel", "normal") and not client.interrupted.is_set():
                self.assertTrue(client.waiting.wait(5), "trial never reached its changed-policy phase")
                client.interrupted.set()
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)

        # Keep the real group dispatcher and player-command route; substitute
        # only the performance module loader so its clock/journal are isolated.
        loader = SimpleNamespace(exec_module=lambda module: None)
        importer = SimpleNamespace(util=SimpleNamespace(
            spec_from_file_location=lambda *args: SimpleNamespace(loader=loader),
            module_from_spec=lambda spec: perf))
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(words=["tt", "perf", "trial", "notifications", "--seconds-per-phase", "10",
                                          "--settle", "0", "--out", str(Path(temp) / "trial")],
                                   json=True, timeout=180, config="/unused/config.json")
            with mock.patch.object(ott, "importlib", importer), \
                    mock.patch.object(ott, "wait", side_effect=interrupt_wait), \
                    mock.patch.object(perf, "time", clock), \
                    mock.patch.object(perf, "Journal", MemoryJournal), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = ott.player_group_command(client, client.config, [("t1", "web")], args, [])
        self.assertNotIn(SECRET, output.getvalue() + errors.getvalue())
        group = json.loads(output.getvalue())
        self.assertEqual(len(group["results"]), 1)
        return code, group["results"][0]["data"], client

    def assert_owned_stop(self, result, client):
        starts = [row for row in client.wire if row[1]["action"] == "perf_capture" and row[1]["params"]["op"] == "start"]
        stops = [row for row in client.wire if row[1]["action"] == "perf_capture" and row[1]["params"]["op"] == "stop"]
        self.assertEqual((len(starts), len(stops)), (1, 1))
        self.assertEqual(stops[0][1]["params"], {"version": 1, "runtime": RUNTIME, "op": "stop",
                                                "session_id": starts[0][1]["params"]["session_id"]})
        self.assertEqual(result["stop"]["session"]["state"], "stopped")
        self.assertEqual(len(client.clones), 2)
        original, cleanup = client.clones
        self.assertTrue(original.group_cancelled.is_set())
        self.assertFalse(cleanup.group_cancelled.is_set())
        self.assertTrue(all(0 < budget <= 5 for worker, _, budget in client.wire if worker is cleanup))
        before = len(client.wire)
        with self.assertRaises(KeyboardInterrupt):
            original.call_receipt("web", "player_settings", {"op": "set", "value": 0})
        self.assertEqual(len(client.wire), before, "cleanup must not reopen ordinary group requests")
        client.opener.open.assert_not_called()

    def test_group_interrupt_wakes_trial_restores_owned_revision_and_stops_owned_capture(self):
        code, result, client = self.run_trial()
        self.assertEqual((code, result["reason"]), (130, "interrupted"))
        self.assertTrue(result["policy_restored"])
        self.assertEqual(client.backend.policy, 1)
        writes = [row[1]["params"] for row in client.wire if row[1]["action"] == "player_settings" and row[1]["params"]["op"] == "set"]
        self.assertEqual([(row["revision"], row["value"]) for row in writes], [("policy-1", 0), ("policy-2", 1)])
        self.assert_owned_stop(result, client)

    def test_normal_group_trial_completes_without_cancelling_ordinary_client(self):
        code, result, client = self.run_trial("normal")
        self.assertEqual(code, 0)
        self.assertNotIn("reason", result)
        self.assertTrue(result["policy_restored"])
        self.assertTrue(result["capture"]["complete"])
        self.assertEqual(len(client.clones), 2)
        self.assertFalse(client.clones[0].group_cancelled.is_set())
        cleanup_requests = [row[1]["params"]["op"] for row in client.wire if row[0] is client.clones[-1]]
        self.assertEqual(cleanup_requests, ["stop"])

    def test_cancellation_during_finally_still_restores_and_stops(self):
        code, result, client = self.run_trial("late_cancel")
        self.assertEqual((code, result["reason"]), (130, "trial_observation_failed"))
        self.assertTrue(result["policy_restored"])
        self.assert_owned_stop(result, client)

    def test_external_revision_change_is_not_overwritten_or_retried(self):
        code, result, client = self.run_trial("stale")
        self.assertEqual(code, 130)
        self.assertIsNone(result["policy_restored"])
        self.assertEqual(client.backend.policy, 0)
        self.assertEqual(result["restore_error"], "restore_not_confirmed")
        writes = [row for row in client.wire if row[1]["action"] == "player_settings" and row[1]["params"]["op"] == "set"]
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[-1][1]["params"]["revision"], "policy-2")
        self.assert_owned_stop(result, client)

    def test_uncertain_committed_write_never_authorizes_a_restore(self):
        code, result, client = self.run_trial("uncertain")
        self.assertEqual(code, 130)
        self.assertTrue(result["unknown_outcome"])
        self.assertIsNone(result["policy_restored"])
        self.assertEqual(client.backend.policy, 0)
        writes = [row for row in client.wire if row[1]["action"] == "player_settings" and row[1]["params"]["op"] == "set"]
        self.assertEqual(len(writes), 1)
        cleanup_requests = [row[1]["action"] for row in client.wire if row[0] is client.clones[-1]]
        self.assertEqual(cleanup_requests, ["perf_capture"])
        self.assert_owned_stop(result, client)

    def test_final_seconds_are_reserved_for_capture_stop_not_restore(self):
        code, result, client = self.run_trial("near_deadline")
        self.assertEqual(code, 130)
        self.assertIsNone(result["policy_restored"])
        self.assertEqual(client.backend.policy, 0)
        self.assertEqual(result["restore_error"], "restore_not_confirmed")
        cleanup_requests = [row for row in client.wire if row[0] is client.clones[-1]]
        self.assertEqual(len(cleanup_requests), 1)
        self.assertEqual(cleanup_requests[0][2], 2)
        self.assert_owned_stop(result, client)

    def test_expired_original_deadline_reports_cleanup_limit_without_more_requests(self):
        code, result, client = self.run_trial("expired")
        self.assertEqual(code, 130)
        self.assertEqual(result["cleanup_error"], "cleanup_deadline_exceeded")
        self.assertEqual(result["stop_error"], "stop_not_confirmed_ttl_applies")
        self.assertIsNone(result["policy_restored"])
        self.assertEqual(len(client.clones), 1)
        self.assertFalse(any(row[1]["params"].get("op") == "stop" for row in client.wire))

    def test_cleanup_authority_is_exact_and_consumed_before_an_uncertain_send(self):
        clock = PerfClock()
        client = TrialTransport(clock, "known")
        client.group_cancelled = threading.Event()
        client.group_cancelled.set()
        params = {"op": "set", "runtime": RUNTIME, "id": "notificationsEnabled", "context": "source-one",
                  "revision": "policy-1", "value": 0}
        with mock.patch.object(perf, "time", clock):
            cleanup = perf._TrialCleanupClient(client, "web", 1015, [("player_settings", params)])
            for device, action, request in [("other", "player_settings", params), ("web", "play", params),
                    ("web", "player_settings", dict(params, runtime="another-runtime")),
                    ("web", "player_settings", dict(params, revision="new-revision")),
                    ("web", "player_settings", dict(params, value=1))]:
                with self.assertRaises(perf.Failure) as failure:
                    cleanup.call_receipt(device, action, request)
                self.assertEqual(failure.exception.reason, "cleanup_request_not_authorized")
            self.assertEqual(client.wire, [])
            client.backend.fail_policy_after_commit = True
            with self.assertRaises(ott.TransportError):
                cleanup.call_receipt("web", "player_settings", params)
            with self.assertRaises(perf.Failure) as failure:
                cleanup.call_receipt("web", "player_settings", params)
            self.assertEqual(failure.exception.reason, "cleanup_request_not_authorized")
        self.assertEqual(len(client.wire), 1)
        self.assertTrue(client.group_cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
