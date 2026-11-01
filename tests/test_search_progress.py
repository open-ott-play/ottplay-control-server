"""Search diagnostics remain live without changing result or failure behavior."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    'search_progress_ott', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class HeartbeatStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.heartbeat = threading.Event()

    def write(self, value):
        result = super().write(value)
        if 'Still working:' in value:
            self.heartbeat.set()
        return result


class SearchProgressTest(unittest.TestCase):
    def test_blocked_work_reports_heartbeat_to_original_stderr(self):
        errors, replacement, output = HeartbeatStream(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                mock.patch.object(ott.SearchProgress, 'heartbeat_seconds', 0.01):
            with ott.SearchProgress('l') as progress:
                progress('Loading programme history', 2, 10)
                # Receipt callbacks and group capture can temporarily redirect
                # global stderr while the outstanding operation is blocked.
                with contextlib.redirect_stderr(replacement):
                    self.assertTrue(errors.heartbeat.wait(1), 'No live heartbeat while waiting')
        self.assertIn('Still working: Loading programme history 2/10', errors.getvalue())
        self.assertIn('Search complete', errors.getvalue())
        self.assertEqual(replacement.getvalue(), '')
        self.assertEqual(output.getvalue(), '')
        self.assertFalse(progress.thread.is_alive())

    def test_exception_and_interrupt_stop_worker_without_replacing_failure(self):
        for failure in (RuntimeError('private failure details'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                errors = io.StringIO()
                with self.assertRaises(type(failure)) as raised:
                    with ott.SearchProgress('l', errors) as progress:
                        progress('Checking archive availability', 1)
                        raise failure
                self.assertIs(raised.exception, failure)
                self.assertFalse(progress.thread.is_alive())
                self.assertTrue(progress.stopped.is_set())
                before = errors.getvalue()
                progress('Late callback must stay silent')
                self.assertEqual(errors.getvalue(), before)
                self.assertIn('Search stopped', before)
                self.assertNotIn('Search complete', before)
                self.assertNotIn('private failure details', before)

    def test_final_counter_is_visible_even_when_intermediate_updates_are_throttled(self):
        errors = io.StringIO()
        with mock.patch.object(ott.time, 'monotonic', return_value=100):
            with ott.SearchProgress('l', errors) as progress:
                progress('Loading programme history', 0, 3)
                progress('Loading programme history', 1, 3)
                progress('Loading programme history', 3, 3)
        self.assertIn('Loading programme history 0/3', errors.getvalue())
        self.assertNotIn('Loading programme history 1/3', errors.getvalue())
        self.assertIn('Loading programme history 3/3', errors.getvalue())
        self.assertEqual(errors.getvalue().count('Search complete'), 1)

    def test_unwritable_or_closed_stderr_does_not_fail_search(self):
        for kind in (OSError, ValueError):
            for failed_operation in ('write', 'flush'):
                with self.subTest(error=kind.__name__, operation=failed_operation):
                    stream = mock.Mock()
                    getattr(stream, failed_operation).side_effect = kind('closed')
                    with ott.SearchProgress('l', stream) as progress:
                        progress('Reading player channel catalogue')
                    self.assertFalse(progress.thread.is_alive())

    def test_player_label_cannot_inject_terminal_control_or_extra_lines(self):
        errors = io.StringIO()
        with ott.SearchProgress('l\n\x1b[31m\r' + 'x' * 200, errors) as progress:
            progress('Reading player channel catalogue')
        self.assertNotIn('\x1b', errors.getvalue())
        self.assertNotIn('\r', errors.getvalue())
        self.assertEqual(len(errors.getvalue().splitlines()), 2)
        self.assertEqual(len(progress.player), 128)
        self.assertIn('[search l  [31m ', errors.getvalue())

    def test_group_progress_is_live_and_json_contains_only_final_results(self):
        errors, output = io.StringIO(), io.StringIO()
        client = mock.Mock(timeout=45, on_receipt=None)
        config = {'epg': {'url': 'https://epg.example/epg/v1', 'source': 'epg-one'}}
        args = SimpleNamespace(words=['t1,t2', 'p', '--list', 'cats'], json=True,
                               timeout=45, refresh=False, config='/unused')
        visited = []

        def search(client, device, settings, query, refresh=False, progress=None):
            self.assertIsNotNone(progress)
            self.assertIs(progress.stream, errors)
            self.assertIsNot(ott.sys.stderr, errors)
            progress('Searching current EPG channels', 0, 2)
            # Assert during the operation, before group output is collected.
            name = {'device1': 't1', 'device2': 't2'}[device]
            self.assertIn('[search ' + name + '] Searching current EPG channels 0/2', errors.getvalue())
            self.assertEqual(ott.sys.stderr.getvalue(), '')
            visited.append(device)
            progress('Searching current EPG channels', 2, 2)
            return {'as_of': 123, 'checked': 2, 'total': 2, 'partial': False, 'programs': []}, {}

        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                mock.patch.object(ott, 'server_programs', side_effect=search):
            status = ott.player_group_command(client, config,
                                              [('t1', 'device1'), ('t2', 'device2')], args, [])
        self.assertEqual(status, 0)
        self.assertEqual(visited, ['device1', 'device2'])
        result = json.loads(output.getvalue())
        self.assertEqual((result['succeeded'], result['failed']), (2, 0))
        self.assertTrue(all('warnings' not in row for row in result['results']))
        self.assertNotIn('[search', output.getvalue())
        self.assertEqual(errors.getvalue().count('Search complete'), 2)
        self.assertEqual(errors.getvalue().count('Searching current EPG channels 0/2'), 2)
        client.call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
