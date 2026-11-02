"""Grouped workbench sampling stops at cancellation, before another observation."""
import contextlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "cli" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load("ott_group_workbench_cancel", "ott.py")
wb = load("workbench_group_cancel", "workbench.py")


class GroupWorkbenchCancellationTest(unittest.TestCase):
    def test_interrupt_releases_all_sampling_gaps_without_another_observation(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {"players": {"t1": "dev_1", "t2": "dev_2"}}
        client.credentials = {"devices": []}
        client.server, client.token = "https://controller.invalid", "fixture-token"
        client.timeout, client.on_receipt, client.opener = 45, None, mock.Mock()
        entered, interrupted = threading.Event(), threading.Event()
        lock = threading.Lock()
        gaps, samples, observed_cancellations, sleeps = [], [], [], []
        original_fork, original_wait = client.for_group, ott.wait

        def mark_gap(device):
            with lock:
                gaps.append(device)
                if len(gaps) == 2:
                    entered.set()

        class Cancellation:
            def __init__(self, event, device):
                self.event, self.device = event, device

            def is_set(self):
                return self.event.is_set()

            def wait(self, timeout):
                self_test.assertEqual(timeout, 30)
                mark_gap(self.device)
                # A deadline bounds failures; it is not a speed assertion.
                cancelled = self.event.wait(5)
                self_test.assertTrue(cancelled, "the group did not signal its waiting worker")
                with lock:
                    observed_cancellations.append(self.device)
                return cancelled

        self_test = self

        def fork(event):
            child = original_fork(event)
            # The workbench observation assigns the device before waiting.
            child.group_cancelled = Cancellation(event, None)
            return child

        def observe(bench, lane):
            self.assertEqual(lane, "web")
            bench.client.group_cancelled.device = bench.device
            with lock:
                samples.append(bench.device)
            return []

        def forbidden_sleep(seconds):
            with lock:
                sleeps.append(seconds)
            mark_gap("uninterruptible sleep")
            raise AssertionError("group sampling must wait on cancellation, not time.sleep")

        def interrupt_wait(*args, **kwargs):
            if not interrupted.is_set():
                self.assertTrue(entered.wait(5), "both workers must reach their sampling gaps")
                interrupted.set()
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)

        # Keep the real dispatcher and workbench entry point, while loading the
        # instrumented module rather than a fresh unpatched copy per worker.
        loader = SimpleNamespace(exec_module=lambda module: None)
        importer = SimpleNamespace(util=SimpleNamespace(
            spec_from_file_location=lambda *args: SimpleNamespace(loader=loader),
            module_from_spec=lambda spec: wb))
        clock = SimpleNamespace(monotonic=lambda: 1000, sleep=forbidden_sleep)
        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(words=["tt", "test", "run", "media-progress", "--lane", "web",
                                          "--duration", "30", "--report", str(Path(temp) / "report")],
                                   json=True, timeout=45, config="/unused/config.json")
            with mock.patch.object(client, "for_group", side_effect=fork), \
                    mock.patch.object(ott, "importlib", importer), \
                    mock.patch.object(ott, "wait", side_effect=interrupt_wait), \
                    mock.patch.object(wb, "time", clock), \
                    mock.patch.object(wb.Workbench, "observe", autospec=True, side_effect=observe), \
                    mock.patch.object(wb, "write_report") as report, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = ott.player_group_command(client, client.config,
                                                [("t1", "dev_1"), ("t2", "dev_2")], args, [])
        result = json.loads(output.getvalue())
        self.assertTrue(interrupted.is_set())
        self.assertEqual(code, 130)
        self.assertEqual((result["succeeded"], result["failed"]), (0, 2))
        self.assertEqual([row["exit_code"] for row in result["results"]], [130, 130])
        self.assertCountEqual(samples, ["dev_1", "dev_2"])
        self.assertCountEqual(observed_cancellations, ["dev_1", "dev_2"])
        self.assertEqual(sleeps, [])
        report.assert_not_called()
        client.opener.open.assert_not_called()

    def test_single_player_keeps_the_requested_gap_and_second_sample(self):
        clock = SimpleNamespace(monotonic=lambda: 1000, sleep=mock.Mock())
        client = SimpleNamespace()
        with mock.patch.object(wb, "time", clock), \
                mock.patch.object(wb.Workbench, "observe", return_value=[]) as observe, \
                mock.patch.object(wb, "write_report", return_value="fixture-report") as report, \
                contextlib.redirect_stdout(io.StringIO()):
            wb.run(ott, client, "dev_1", ["test", "run", "media-progress", "--lane", "web",
                                        "--duration", "30", "--report", "fixture-report"], 45, True)
        clock.sleep.assert_called_once_with(30)
        self.assertEqual(observe.call_count, 2)
        report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
