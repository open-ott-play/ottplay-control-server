"""Group workers isolate authority/output and stop without replaying changes."""
import contextlib
import importlib.util
import io
import json
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'ott_group_worker_isolation', Path(__file__).resolve().parents[1] / 'cli' / 'ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class GroupWorkerIsolationTest(unittest.TestCase):
    def client(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {f't{i}': f'dev_{i}' for i in range(1, 5)},
                         'nested': {'values': ['original']}}
        client.credentials = {'devices': [{'id': 'dev_1', 'token': 'private-fixture-token'}]}
        client.server = 'https://controller.invalid'
        client.token = 'private-fixture-admin'
        client.timeout = 45
        client.on_receipt = None
        client.opener = mock.Mock()
        return client

    def group(self, client, runner):
        output, errors = io.StringIO(), io.StringIO()
        args = SimpleNamespace(words=['tt', 'go'], json=True, timeout=45, config='/unused/config.json')
        targets = [(f't{i}', f'dev_{i}') for i in range(1, 5)]
        with mock.patch.object(ott, 'execute_player_command', side_effect=runner), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.player_group_command(client, client.config, targets, args, [])
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_forks_keep_budgets_credentials_config_callbacks_and_openers_private(self):
        client = self.client()
        callback = mock.Mock()
        client.on_receipt = callback
        stopped = threading.Event()
        with mock.patch.object(ott, 'read_json', side_effect=AssertionError('must not reload credentials')):
            first, second = client.for_group(stopped), client.for_group(stopped)
        first.timeout = 1
        first.on_receipt = None
        first.config['nested']['values'].append('changed')
        first.credentials['devices'][0]['token'] = 'changed'
        self.assertEqual((client.timeout, second.timeout), (45, 45))
        self.assertIs(client.on_receipt, callback)
        self.assertIs(second.on_receipt, callback)
        for other in (client, second):
            self.assertEqual(other.config['nested']['values'], ['original'])
            self.assertEqual(other.credentials['devices'][0]['token'], 'private-fixture-token')
        self.assertIsNot(first.opener, second.opener)
        self.assertIsNot(first.opener, client.opener)
        self.assertIs(first.group_cancelled, stopped)
        self.assertIs(second.group_cancelled, stopped)

    def test_forced_partial_writes_keep_results_warnings_receipts_and_progress_separate(self):
        client = self.client()
        barriers = [threading.Barrier(4) for _ in range(2)]
        receipt_started, progress_attempted = threading.Event(), threading.Event()

        def receipt(item):
            encoded = json.dumps(item)
            middle = len(encoded) // 2
            ott.sys.stderr.write(encoded[:middle])
            if item['player'] == 't1':
                receipt_started.set()
                self.assertTrue(progress_attempted.wait(5), 'background writer never attempted its line')
            ott.sys.stderr.write(encoded[middle:] + '\n')

        client.on_receipt = receipt

        def run(local, config, args, argv, device, **options):
            ott.sys.stdout.write('{"device":')
            barriers[0].wait(5)
            ott.sys.stdout.write(json.dumps(device) + '}\n')
            ott.sys.stderr.write('warning ')
            barriers[1].wait(5)
            ott.sys.stderr.write(device + '\n')
            background = None
            if device == 'dev_1':
                def progress():
                    if receipt_started.wait(5):
                        progress_attempted.set()
                        print(json.dumps({'progress': 'background'}), file=options['progress_stream'], flush=True)
                background = threading.Thread(target=progress, daemon=True)
                background.start()
            local.on_receipt({'device': device, 'status': 'ok'})
            if background is not None:
                background.join(5)
                self.assertFalse(background.is_alive(), 'background progress did not finish')
            return 0

        code, result, errors = self.group(client, run)
        self.assertEqual((code, result['succeeded'], result['failed']), (0, 4, 0))
        for number, row in enumerate(result['results'], 1):
            self.assertEqual(row['data'], {'device': f'dev_{number}'})
            self.assertEqual(row['warnings'], [f'warning dev_{number}'])
        diagnostics = [json.loads(line) for line in errors.splitlines()]
        self.assertEqual(len(diagnostics), 5)
        receipts = {row['player']: row['device'] for row in diagnostics if 'player' in row}
        self.assertEqual(receipts, {f't{i}': f'dev_{i}' for i in range(1, 5)})
        self.assertIn({'progress': 'background'}, diagnostics)
        self.assertIs(client.on_receipt, receipt)

    def test_cancelled_group_rejects_api_call_before_starting_transport(self):
        client = self.client()
        client.group_cancelled = threading.Event()
        client.group_cancelled.set()
        with mock.patch.object(client, '_api') as transport, self.assertRaises(KeyboardInterrupt):
            client.api('/api/requests', {'action': 'play', 'params': {'query': '1'}})
        transport.assert_not_called()
        client.opener.open.assert_not_called()

    def test_cancellation_between_api_entry_and_transport_open_sends_no_post(self):
        client = self.client()
        client.group_cancelled = threading.Event()
        constructed, release, transport_done = threading.Event(), threading.Event(), threading.Event()
        original_request, original_transport = ott.urllib.request.Request, client._api
        outcomes = []

        def request(*args, **kwargs):
            value = original_request(*args, **kwargs)
            constructed.set()
            if not release.wait(5):
                raise AssertionError('transport construction was never released')
            return value

        def transport(*args, **kwargs):
            try:
                return original_transport(*args, **kwargs)
            finally:
                transport_done.set()

        def call():
            try:
                client.api('/api/requests', {'action': 'play', 'params': {'query': '1'}})
            except (KeyboardInterrupt, ott.Error, AssertionError) as error:
                outcomes.append(error)

        with mock.patch.object(ott.urllib.request, 'Request', side_effect=request), \
                mock.patch.object(client, '_api', side_effect=transport):
            worker = threading.Thread(target=call, daemon=True)
            worker.start()
            try:
                self.assertTrue(constructed.wait(5), 'transport never reached request construction')
                client.group_cancelled.set()
            finally:
                release.set()
                worker.join(5)
                self.assertTrue(transport_done.wait(5), 'transport worker did not retire')
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], (KeyboardInterrupt, ott.TransportError))
        client.opener.open.assert_not_called()

    def test_transport_itself_rechecks_group_cancellation(self):
        client = self.client()
        client.group_cancelled = threading.Event()
        client.group_cancelled.set()
        with self.assertRaises(ott.TransportError):
            client._api('/api/requests', {'action': 'play'}, 1,
                        time.monotonic() + 1, threading.Event())
        client.opener.open.assert_not_called()

    def test_interrupt_during_submission_preserves_already_executed_outcomes(self):
        for execute_second in (False, True):
            with self.subTest(execute_second=execute_second):
                calls = []

                class Pool:
                    def __init__(self, execute_second=execute_second, **kwargs):
                        self.count = 0
                        self.execute_second = execute_second

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def submit(self, function, index):
                        self.count += 1
                        if self.count == 2 and not self.execute_second:
                            raise KeyboardInterrupt
                        future = Future()
                        future.set_result(function(index))
                        if self.count == 2:
                            # The worker completed before submit was interrupted;
                            # its request must never be reported as not started.
                            raise KeyboardInterrupt
                        return future

                def run(local, config, args, argv, device, calls=calls, **options):
                    calls.append(device)
                    print(json.dumps({'device': device}))
                    return 0

                with mock.patch.object(ott, 'ThreadPoolExecutor', Pool):
                    code, result, _ = self.group(self.client(), run)
                completed = 2 if execute_second else 1
                self.assertEqual(code, 130)
                self.assertEqual(calls, [f'dev_{i}' for i in range(1, completed + 1)])
                self.assertEqual(result['succeeded'], completed)
                for index, row in enumerate(result['results']):
                    self.assertEqual(row['ok'], index < completed)
                    if index >= completed:
                        self.assertEqual(row['exit_code'], 130)
                        self.assertIn('Not started', row['error'])

    def test_interrupt_during_result_collection_does_not_lose_completed_rows(self):
        calls = []
        interrupted = threading.Event()

        class InterruptedFuture(Future):
            def result(self, timeout=None):
                if not interrupted.is_set():
                    interrupted.set()
                    raise KeyboardInterrupt
                return super().result(timeout)

        class Pool:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def submit(self, function, index):
                future = InterruptedFuture()
                future.set_result(function(index))
                return future

        def run(local, config, args, argv, device, **options):
            calls.append(device)
            print(json.dumps({'device': device}))
            return 0

        with mock.patch.object(ott, 'ThreadPoolExecutor', Pool), \
                mock.patch.object(ott, 'wait', side_effect=lambda pending, **kwargs: ({next(iter(pending))}, set())):
            code, result, _ = self.group(self.client(), run)
        self.assertTrue(interrupted.is_set(), 'collection did not exercise the interrupt boundary')
        self.assertEqual(code, 130)
        self.assertEqual(calls, [f'dev_{i}' for i in range(1, 5)])
        self.assertEqual((result['succeeded'], result['failed']), (4, 0))
        self.assertEqual([row['data']['device'] for row in result['results']], calls)


if __name__ == '__main__':
    unittest.main()
