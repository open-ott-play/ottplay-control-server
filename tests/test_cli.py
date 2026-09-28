import importlib.util
import base64
import contextlib
import io
import http.server
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location('ott', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

class CliTest(unittest.TestCase):
    def test_volume(self):
        self.assertEqual(ott.parse_command(['V']), ('status', {}))
        for text, field, value in [('35','volume',35),('+5','volume_step',5),('-12','volume_step',-12),('0','volume',0)]:
            self.assertEqual(ott.parse_command(['v',text]), ('command',{'command':'set_volume',field:value}))
        for value in ['nan','inf','1e999','101','1 2']:
            with self.assertRaises(ott.Error): ott.parse_command(['v',value])
    def test_queries(self):
        self.assertEqual(ott.parse_command(['S','НовоСТИ']),('channels',{'search':'НовоСТИ'}))
        self.assertEqual(ott.parse_command(['p','НАУКА']),('programs',{'search':'НАУКА'}))
        self.assertEqual(ott.parse_command(['12']),('play',{'query':'12'}))
        self.assertEqual(ott.parse_command(['Первый','HD']),('play',{'query':'Первый HD'}))
        self.assertEqual(ott.parse_command(['play','s']),('play',{'query':'s'}))
        self.assertEqual(ott.parse_command(['provider','M3U']),('provider',{'query':'M3U'}))
    def test_private_config_and_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'server.json'
            ott.write_private(path, {'admin_token':'a'*32,'devices':[{'id':'dev_abc','token':'b'*32}]})
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            client=ott.Client({'server':'http://127.0.0.1:8081','server_config':str(path),'players':{'TV':'dev_abc'}})
            self.assertEqual(client.device('tv'),'dev_abc')
            with self.assertRaises(ott.Error): client.device('missing')
    def test_terminal_control(self):
        self.assertNotIn('\x1b',ott.clean('\x1b[31mnews\n'))

    def test_partial_epg_json_retains_failure_exit_status(self):
        client = mock.Mock()
        client.device.return_value = 'dev_tv'
        result = {'programs': [{'channel': 'News', 'title': 'Current'}], 'partial': True, 'checked': 1, 'total': 2}
        client.call.return_value = result
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value={}), mock.patch.object(ott, 'Client', return_value=client), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = ott.main(['--json', 'tv', 'p'])
        self.assertEqual(status, 3)
        self.assertEqual(json.loads(output.getvalue()), result)
        self.assertIn('checked 1 of 2', errors.getvalue())

    def test_incomplete_http_body_is_a_retryable_transport_failure(self):
        client = object.__new__(ott.Client)
        client.timeout = 5
        client.token = 'a' * 32
        client.server = 'http://127.0.0.1:8081'
        client.opener = mock.Mock()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read1.side_effect = ott.http.client.IncompleteRead(b'partial')
        client.opener.open.return_value = response
        with self.assertRaises(ott.TransportError):
            client.api('/api/requests?device_id=tv&id=' + 'a' * 32)

    def test_trickling_http_body_cannot_return_success_after_call_deadline(self):
        stop = threading.Event()
        posts = []
        class SlowBody(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                posts.append(self.rfile.read(int(self.headers['Content-Length'])))
                body = json.dumps({'id': 'a' * 32}).encode()
                self.send_response(202)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_GET(self):
                body = json.dumps({'status': 'ok', 'data': {'volume': 35}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                for byte in body:
                    if stop.wait(0.06):
                        return
                    try:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), SlowBody)
        server.daemon_threads = True
        listener = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        listener.start()
        client = object.__new__(ott.Client)
        client.timeout = 1.2
        client.token = 'a' * 32
        client.server = f'http://127.0.0.1:{server.server_port}'
        client.opener = ott.urllib.request.build_opener(ott.urllib.request.ProxyHandler({}))
        try:
            start = time.monotonic()
            with self.assertRaisesRegex(ott.Error, 'do not repeat'):
                client.call('tv', 'command', {'command': 'set_volume', 'volume_step': 5})
            self.assertLess(time.monotonic() - start, 1.8)
            self.assertEqual(len(posts), 1)
            # Keep the body trickling while checking cancellation, so closing
            # the fixture cannot conceal a worker that ignores its deadline.
            for thread in threading.enumerate():
                if thread.name == 'ottplay-http':
                    thread.join(0.8)
                    self.assertFalse(thread.is_alive())
        finally:
            stop.set()
            server.shutdown()
            server.server_close()
            listener.join(1)

    def test_short_http_body_retries_only_the_same_result_id(self):
        posts, reads = [], []
        class ShortThenComplete(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                posts.append(self.rfile.read(int(self.headers['Content-Length'])))
                body = json.dumps({'id': 'a' * 32}).encode()
                self.send_response(202)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_GET(self):
                reads.append(self.path)
                body = json.dumps({'status': 'ok', 'data': {'volume': 35}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                # HTTP/1.0 closes the first response after ten bytes while the
                # declared length still promises the complete JSON envelope.
                self.wfile.write(body[:10] if len(reads) == 1 else body)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), ShortThenComplete)
        server.daemon_threads = True
        listener = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        listener.start()
        client = object.__new__(ott.Client)
        client.timeout = 5
        client.token = 'a' * 32
        client.server = f'http://127.0.0.1:{server.server_port}'
        client.opener = ott.urllib.request.build_opener(ott.urllib.request.ProxyHandler({}))
        try:
            self.assertEqual(client.call('tv', 'command', {'command': 'set_volume', 'volume_step': 5}), {'volume': 35})
            self.assertEqual(len(posts), 1)
            self.assertEqual(len(reads), 2)
            self.assertEqual(reads[0], reads[1])
            self.assertTrue(reads[0].endswith('&id=' + 'a' * 32))
        finally:
            server.shutdown()
            server.server_close()
            listener.join(1)


class ReadbackTest(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(ott.Client)
        self.client.timeout = 5
        self.now = 0
        self.clock = mock.patch.object(ott.time, 'monotonic', side_effect=lambda: self.now)
        self.sleep = mock.patch.object(ott.time, 'sleep', side_effect=self.advance)
        self.clock.start()
        self.sleep.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)

    def advance(self, duration):
        self.now += duration

    def test_transient_readback_retries_same_id_without_reposting(self):
        self.client.api = mock.Mock(side_effect=[
            (202, {'id': 'a' * 32}), ott.TransportError('lost readback'),
            ott.HTTPError(503), (200, {'status': 'ok', 'data': {'volume': 40}})])
        self.assertEqual(self.client.call('tv', 'command', {'volume_step': 5}), {'volume': 40})
        calls = self.client.api.call_args_list
        self.assertEqual(len(calls[0].args), 2)
        self.assertTrue(all(len(call.args) == 1 for call in calls[1:]))
        self.assertEqual(len({call.args[0] for call in calls[1:]}), 1)

    def test_total_budget_includes_enqueue_and_caps_last_poll(self):
        self.client.timeout = 2
        def api(path, payload=None, timeout=None):
            if payload is not None:
                self.advance(0.2)
                return 202, {'id': 'b' * 32}
            self.assertAlmostEqual(timeout, 1)
            self.advance(timeout)
            raise ott.TransportError('read timed out')
        self.client.api = mock.Mock(side_effect=api)
        with self.assertRaisesRegex(ott.Error, 'do not repeat'):
            self.client.call('tv', 'status', {})
        self.assertAlmostEqual(self.now, 2)
        self.assertEqual(self.client.api.call_count, 2)

    def test_no_poll_after_enqueue_consumes_budget(self):
        self.client.timeout = 1
        def api(*args, **kwargs):
            self.advance(0.6)
            return 202, {'id': 'b' * 32}
        self.client.api = mock.Mock(side_effect=api)
        with self.assertRaises(ott.Error):
            self.client.call('tv', 'status', {})
        self.assertAlmostEqual(self.now, 1)
        self.client.api.assert_called_once()

    def test_lost_enqueue_is_not_replayed_and_explains_uncertainty(self):
        self.client.api = mock.Mock(side_effect=ott.TransportError('lost enqueue'))
        with self.assertRaisesRegex(ott.Error, 'may have been accepted'):
            self.client.call('tv', 'command', {'volume_step': 5})
        self.client.api.assert_called_once()

    def test_permanent_readback_failure_is_not_retried(self):
        self.client.api = mock.Mock(side_effect=[(202, {'id': 'a' * 32}), ott.HTTPError(401)])
        with self.assertRaises(ott.HTTPError):
            self.client.call('tv', 'status', {})
        self.assertEqual(self.client.api.call_count, 2)

    def test_expired_receipt_does_not_claim_an_old_server(self):
        self.client.api = mock.Mock(side_effect=[(202, {'id': 'a' * 32}), ott.HTTPError(404)])
        with self.assertRaisesRegex(ott.Error, 'The request receipt has expired.*may have been executed'):
            self.client.call('tv', 'command', {'volume_step': 5})
        self.assertEqual(self.client.api.call_count, 2)

    def test_malformed_readback_is_reported_without_traceback_or_repost(self):
        for status, result in [(200, []), (200, {'status': 'ok'}), (200, {'status': 'ok', 'data': []}),
                               (202, []), (202, {'status': 'unknown'})]:
            with self.subTest(status=status, result=result):
                self.client.api = mock.Mock(side_effect=[(202, {'id': 'a' * 32}), (status, result)])
                with self.assertRaisesRegex(ott.Error, 'may have been executed'):
                    self.client.call('tv', 'command', {'volume_step': 5})
                self.assertEqual(self.client.api.call_count, 2)


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config_path = Path(directory.name) / 'cli.json'
        self.server_path = Path(directory.name) / 'server.json'
        self.journal_path = Path(str(self.config_path) + '.pending-add.json')
        self.credentials = {'admin_token': 'a' * 32, 'devices': [], 'allowed_origins': ['https://player.example']}
        self.config = {'server': 'http://127.0.0.1:8081', 'server_config': str(self.server_path),
                       'players': {'other': 'existing'}, 'unrelated': 'preserved',
                       'kubernetes': {'context': 'test', 'namespace': 'test', 'secret': 'test', 'deployment': 'test'}}
        ott.write_private(self.server_path, self.credentials)
        ott.write_private(self.config_path, self.config)
        self.live = self.credentials
        self.replaces = 0
        self.restarts = 0
        self.failure = None
        self.edit_during_replace = None
        self.edit_during_rollout = None
        self.get = mock.patch.object(ott.subprocess, 'check_output', side_effect=self.get_secret)
        self.run = mock.patch.object(ott.subprocess, 'run', side_effect=self.run_kubectl)
        self.get.start()
        self.run.start()
        self.addCleanup(self.get.stop)
        self.addCleanup(self.run.stop)

    def get_secret(self, args, **kwargs):
        self.assertEqual(kwargs['timeout'], 20)
        return json.dumps({'metadata': {'resourceVersion': '9'}, 'data': {
            'config.json': base64.b64encode(json.dumps(self.live).encode()).decode()}}).encode()

    def run_kubectl(self, args, **kwargs):
        self.assertIn('--request-timeout=15s', args)
        self.assertGreater(kwargs['timeout'], 0)
        if 'replace' in args:
            self.replaces += 1
            resource = json.loads(kwargs['input'])
            self.assertEqual(resource['metadata']['resourceVersion'], '9')
            self.live = json.loads(base64.b64decode(resource['data']['config.json']))
            if self.edit_during_replace:
                self.edit_during_replace()
            if self.failure == 'replace_response':
                self.failure = None
                raise subprocess.CalledProcessError(1, args)
        elif 'status' in args and self.edit_during_rollout:
            self.edit_during_rollout()
        elif 'restart' in args:
            self.restarts += 1
            if self.failure == 'restart':
                self.failure = None
                raise subprocess.CalledProcessError(1, args)

    def add(self, name='tv', device='dev_tv'):
        client = ott.Client(ott.read_json(self.config_path))
        with contextlib.redirect_stdout(io.StringIO()):
            ott.management(client, self.config_path, ['add', name, device])

    def assert_recovered(self, token):
        self.add()
        self.assertFalse(self.journal_path.exists())
        self.assertEqual(self.replaces, 1)
        self.assertEqual(self.live, ott.read_json(self.server_path))
        self.assertEqual(self.live['devices'], [{'id': 'dev_tv', 'token': token}])
        self.assertEqual(self.live['allowed_origins'], self.credentials['allowed_origins'])
        config = ott.read_json(self.config_path)
        self.assertEqual(config['players'], {'other': 'existing', 'tv': 'dev_tv'})
        self.assertEqual(config['unrelated'], 'preserved')

    def test_restart_failure_resumes_with_same_token(self):
        self.failure = 'restart'
        with self.assertRaisesRegex(ott.Error, 'Retry ott add tv dev_tv'):
            self.add()
        self.assertTrue(self.journal_path.exists())
        if os.name == 'posix':
            self.assertEqual(self.journal_path.stat().st_mode & 0o777, 0o600)
        token = self.live['devices'][0]['token']
        self.assert_recovered(token)
        self.assertEqual(self.restarts, 2)

    def test_lost_replace_response_reconciles_without_replacing_again(self):
        self.failure = 'replace_response'
        with self.assertRaises(ott.Error):
            self.add()
        self.assertEqual(ott.read_json(self.server_path), self.credentials)
        self.assert_recovered(self.live['devices'][0]['token'])

    def test_local_save_failure_preserves_token_for_reconciliation(self):
        original = ott.write_private
        def fail_save(path, *args, **kwargs):
            if str(path) == str(self.server_path):
                raise OSError('simulated full disk')
            return original(path, *args, **kwargs)
        with mock.patch.object(ott, 'write_private', side_effect=fail_save), self.assertRaises(ott.Error):
            self.add()
        self.assert_recovered(self.live['devices'][0]['token'])

    def test_resume_refuses_external_config_change(self):
        self.failure = 'restart'
        with self.assertRaises(ott.Error):
            self.add()
        self.live['allowed_origins'].append('https://other.example')
        with self.assertRaisesRegex(ott.Error, 'The cluster configuration changed'):
            self.add()
        self.assertEqual(self.replaces, 1)
        self.assertEqual(self.restarts, 1)
        self.assertTrue(self.journal_path.exists())

    def test_different_add_does_not_overwrite_pending_operation(self):
        self.failure = 'restart'
        with self.assertRaises(ott.Error):
            self.add()
        before = self.journal_path.read_bytes()
        with self.assertRaisesRegex(ott.Error, 'unfinished ott add'):
            self.add('mac', 'dev_mac')
        self.assertEqual(self.journal_path.read_bytes(), before)
        self.assertEqual(self.replaces, 1)

    def test_malformed_journal_is_rejected_before_kubectl(self):
        for data in (None, [], {}, {'version': 2}, {'version': 1, 'identity': {}, 'before': {}, 'after': []}):
            with self.subTest(data=data):
                ott.write_private(self.journal_path, data)
                with self.assertRaisesRegex(ott.Error, 'Invalid pending-add.json'):
                    self.add()
                self.assertEqual(ott.read_json(self.journal_path), data)
                self.assertEqual(self.replaces, 0)
                self.assertEqual(self.restarts, 0)

    def edit_config(self, update):
        current = ott.read_json(self.config_path)
        update(current)
        ott.write_private(self.config_path, current)

    def add_concurrent_fields(self):
        def update(current):
            current['players']['new_alias'] = 'existing'
            current['unrelated'] = 'concurrent-change'
        self.edit_config(update)

    def assert_concurrent_fields_preserved(self):
        self.add()
        current = ott.read_json(self.config_path)
        self.assertEqual(current['players'], {'other': 'existing', 'new_alias': 'existing', 'tv': 'dev_tv'})
        self.assertEqual(current['unrelated'], 'concurrent-change')
        self.assertFalse(self.journal_path.exists())

    def test_concurrent_config_edits_during_replace_are_preserved(self):
        self.edit_during_replace = self.add_concurrent_fields
        self.assert_concurrent_fields_preserved()

    def test_concurrent_config_edits_during_rollout_are_preserved(self):
        self.edit_during_rollout = self.add_concurrent_fields
        self.assert_concurrent_fields_preserved()

    def test_conflicting_target_alias_keeps_journal_and_can_resume(self):
        self.edit_during_replace = lambda: self.edit_config(lambda current: current['players'].update({'TV': 'different'}))
        with self.assertRaisesRegex(ott.Error, 'The target alias'):
            self.add()
        self.assertTrue(self.journal_path.exists())
        self.assertEqual(ott.read_json(self.config_path)['players']['TV'], 'different')
        token = self.live['devices'][0]['token']
        self.edit_config(lambda current: current['players'].pop('TV'))
        self.assert_recovered(token)

    def assert_identity_drift_keeps_journal(self, field, value):
        self.edit_during_replace = lambda: self.edit_config(lambda current: current.update({field: value}))
        with self.assertRaisesRegex(ott.Error, 'The server address or Kubernetes/server_config'):
            self.add()
        self.assertTrue(self.journal_path.exists())
        self.assertEqual(ott.read_json(self.config_path)[field], value)
        self.assertEqual(self.restarts, 0)
        # Restoring only the conflicting target permits recovery with the same token.
        self.edit_config(lambda current: current.update({field: self.config[field]}))
        self.assert_recovered(self.live['devices'][0]['token'])

    def test_concurrent_server_change_is_not_overwritten(self):
        self.assert_identity_drift_keeps_journal('server', 'https://different.example')

    def test_concurrent_kubernetes_change_is_not_overwritten(self):
        self.assert_identity_drift_keeps_journal('kubernetes', {**self.config['kubernetes'], 'context': 'different'})

    def test_concurrent_server_config_change_is_not_overwritten(self):
        self.assert_identity_drift_keeps_journal('server_config', str(self.server_path.with_name('different.json')))

    def test_target_change_during_rollout_prevents_journal_retirement(self):
        self.edit_during_rollout = lambda: self.edit_config(lambda current: current['players'].update({'tv': 'different'}))
        with self.assertRaisesRegex(ott.Error, 'The target alias'):
            self.add()
        self.assertTrue(self.journal_path.exists())
        self.assertEqual(ott.read_json(self.config_path)['players']['tv'], 'different')
        self.assertEqual(self.restarts, 1)

if __name__=='__main__': unittest.main()
