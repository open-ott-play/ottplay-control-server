"""Group cancellation wakes catalog rate-limit waits without replaying reads."""
import contextlib
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


spec = importlib.util.spec_from_file_location(
    'ott_group_catalog_cancel', Path(__file__).resolve().parents[1] / 'cli' / 'ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class GroupCatalogCancellationTest(unittest.TestCase):
    def test_interrupt_wakes_each_catalog_backoff_without_another_request(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'t1': 'dev_1', 't2': 'dev_2'}}
        client.credentials = {}
        client.timeout = 45
        client.on_receipt = None
        entered = threading.Barrier(3, timeout=5)
        calls, pauses = [], []
        lock = threading.Lock()
        fork = ott.Client.for_group
        interrupted = False
        original_wait = ott.wait

        class WatchingCancellation:
            def __init__(self, stopped):
                self.stopped = stopped

            def is_set(self):
                return self.stopped.is_set()

            def wait(self, seconds):
                with lock:
                    pauses.append(seconds)
                entered.wait()
                return self.stopped.wait(5)

        def grouped(original, stopped):
            child = fork(original, stopped)
            child.group_cancelled = WatchingCancellation(stopped)
            return child

        def rejected(local, device, action, params):
            with lock:
                calls.append((device, action, params))
            # The server declined the submission: no request was queued.
            raise ott.HTTPError(429)

        def interrupt_when_waiting(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                entered.wait()
                interrupted = True
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)

        args = SimpleNamespace(words=['tt', 'categories'], json=True,
                               timeout=45, config='/unused/config.json')
        output = io.StringIO()
        with mock.patch.object(ott.Client, 'for_group', grouped), \
                mock.patch.object(ott.Client, 'call', rejected), \
                mock.patch.object(ott, 'wait', side_effect=interrupt_when_waiting), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = ott.player_group_command(client, client.config,
                                            [('t1', 'dev_1'), ('t2', 'dev_2')], args, [])
        self.assertTrue(interrupted)
        self.assertEqual(code, 130)
        self.assertEqual(sorted(calls), [('dev_1', 'capabilities', {}),
                                         ('dev_2', 'capabilities', {})])
        self.assertEqual(pauses, [1, 1])
        result = json.loads(output.getvalue())
        self.assertEqual([row['exit_code'] for row in result['results']], [130, 130])
        self.assertFalse(any(row['ok'] for row in result['results']))


if __name__ == '__main__':
    unittest.main()
