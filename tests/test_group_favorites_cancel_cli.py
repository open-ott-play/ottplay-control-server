"""Favorites pacing observes the group cancellation event before another RPC."""
from collections import deque
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    import favorites_commands as commands
    import favorites_remote as remote


class ObservedCancellation:
    def __init__(self):
        self.event = threading.Event()
        self.entered = threading.Event()
        self.delays = []

    def is_set(self):
        return self.event.is_set()

    def wait(self, delay):
        self.delays.append(delay)
        self.entered.set()
        return self.event.wait(delay)


class GroupFavoritesCancellationTest(unittest.TestCase):
    def waiter(self, kind, client, clock, sleep):
        if kind == "discovery":
            target = commands._Target.__new__(commands._Target)
            target.client, target.device = client, "device-one"
            target.clock, target.sleep = clock, sleep
            target.last_clock, target.deadline = None, clock() + 120
            return target, target.wait
        target = remote._RequestTransport.__new__(remote._RequestTransport)
        target.client, target.clock, target.sleep = client, clock, sleep
        target._clock_last = None
        return target, lambda until: target._wait(until, clock() + 120)

    def test_already_cancelled_group_does_not_enter_even_ready_wait(self):
        for kind in ("discovery", "transport"):
            with self.subTest(kind=kind):
                cancelled = ObservedCancellation()
                cancelled.event.set()
                sleeper = mock.Mock(side_effect=AssertionError("uncancellable sleep"))
                _, wait = self.waiter(kind, SimpleNamespace(group_cancelled=cancelled), lambda: 1, sleeper)
                with self.assertRaises(KeyboardInterrupt):
                    wait(1)
                self.assertEqual(cancelled.delays, [])
                sleeper.assert_not_called()

    def test_cancellation_wakes_pacing_and_prevents_the_next_rpc(self):
        for kind in ("discovery", "transport"):
            with self.subTest(kind=kind):
                cancelled = ObservedCancellation()
                client = SimpleNamespace(group_cancelled=cancelled, api=mock.Mock(), api_raw=mock.Mock())
                sleeper = mock.Mock(side_effect=AssertionError("uncancellable sleep"))
                target, _ = self.waiter(kind, client, time.monotonic, sleeper)
                if kind == "discovery":
                    action = target.read_capabilities
                else:
                    # A full HTTP rate window can otherwise keep a cancelled
                    # group waiting for a minute before it checks Client.api.
                    now = time.monotonic()
                    target._last_http = target._last_post = now
                    target._http_times = deque([now] * 120)
                    action = lambda: (target._slot(post=True, deadline=now + 120), client.api("unused"))
                outcome = queue.Queue(maxsize=1)

                def worker():
                    try:
                        action()
                    except BaseException as error:
                        outcome.put(error)
                    else:
                        outcome.put(None)

                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                try:
                    self.assertTrue(cancelled.entered.wait(5), "worker did not enter cancellable pacing")
                    cancelled.event.set()
                    self.assertIsInstance(outcome.get(timeout=5), KeyboardInterrupt)
                    self.assertEqual(len(cancelled.delays), 1)
                    self.assertGreater(cancelled.delays[0], 1)
                    client.api.assert_not_called()
                    client.api_raw.assert_not_called()
                    sleeper.assert_not_called()
                finally:
                    cancelled.event.set()
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_non_group_pacing_preserves_injected_clock_and_sleeper(self):
        for kind in ("discovery", "transport"):
            with self.subTest(kind=kind):
                now = [1.0]
                delays = []

                def sleep(delay):
                    delays.append(delay)
                    now[0] += delay

                _, wait = self.waiter(kind, SimpleNamespace(), lambda: now[0], sleep)
                wait(61)
                self.assertEqual(delays, [5] * 12)
                self.assertEqual(now[0], 61)


if __name__ == "__main__":
    unittest.main()
