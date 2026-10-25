import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'cli' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load('registry_ott', 'ott.py')
registry = load('registry_module', 'device_registry.py')


class RegistryClient:
    def __init__(self, config, path):
        self.config, self.path = config, path
        self.timeout, self.server, self.token = 45, config['server'], 'a' * 32
        self.credentials = ott.read_json(config['server_config'])
        self.rows, self.operations, self.calls = {}, {}, []
        self.store_id, self.next_generation = 'b' * 32, 1
        self.fail_next, self.probe_error, self.after_write = None, None, None
        self.pairings = []

    def snapshot(self):
        return {'version': 1, 'store_id': self.store_id, 'revision': self.next_generation,
                'devices': copy.deepcopy(list(self.rows.values())), 'limits': dict(registry.LIMITS)}

    def api(self, path, payload=None, timeout=None):
        self.calls.append((path, copy.deepcopy(payload)))
        if timeout is None or timeout <= 0:
            raise AssertionError('every registry call must share a bounded timeout')
        if path == '/api/device-registry':
            if self.probe_error:
                raise self.probe_error
            return 200, self.snapshot()
        if path.startswith('/api/device-registry/operations?'):
            key = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['id'][0]
            if key not in self.operations:
                raise ott.HTTPError(404)
            return 200, copy.deepcopy(self.operations[key])
        if path.startswith('/api/device-registry/credential?'):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            row = self.rows[q['device_id'][0]]
            return 200, {'version': 1, 'device_id': row['device_id'], 'generation': row['generation'], 'token': 'z' * 43}
        if path == '/api/pairings':
            return 200, {'pairings': copy.deepcopy(self.pairings)}
        if payload is None:
            raise AssertionError(path)
        checkpoint = self.path.with_name(self.path.name + '.registry-pending.json')
        saved = json.loads(checkpoint.read_text())
        sent = dict(saved['request'])
        if path == '/api/pairings/approve':
            sent.pop('version')
            sent.pop('device_id')
        if sent != payload:
            raise AssertionError('request was not durably saved before sending')
        payload = saved['request']
        if self.fail_next == 'before':
            self.fail_next = None
            raise ott.TransportError('network unavailable')
        kind = path.rsplit('/', 1)[1]
        if payload['operation_id'] not in self.operations:
            generation = f'{self.next_generation:032x}'
            self.next_generation += 1
            state = 'revoked' if kind == 'revoke' else 'active'
            self.rows[payload['device_id']] = {'device_id': payload['device_id'], 'generation': generation,
                                             'state': state, 'origin': 'dynamic', 'diagnostics_enabled': False}
            result = {'version': 1, 'operation_id': payload['operation_id'], 'device_id': payload['device_id'],
                      'generation': generation, 'state': state, 'persisted': True}
            if kind == 'revoke':
                result['device_stop_confirmed'] = False
            self.operations[payload['operation_id']] = result
        if self.after_write:
            self.after_write()
        if self.fail_next == 'after':
            self.fail_next = None
            raise ott.TransportError('response lost')
        return 200, {'status': 'approved'} if kind == 'approve' else copy.deepcopy(self.operations[payload['operation_id']])


class DeviceRegistryCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cli.json'
        self.credentials = Path(self.temp.name) / 'server.json'
        self.original_credentials = {'admin_token': 'a' * 32, 'devices': []}
        ott.write_private(self.credentials, self.original_credentials)
        self.config = {'server': 'https://controller.example/ott-control', 'server_config': str(self.credentials), 'players': {}}
        ott.write_private(self.path, self.config)
        self.client = RegistryClient(self.config, self.path)
        self.checkpoint = self.path.with_name(self.path.name + '.registry-pending.json')

    def run_command(self, *words, json_output=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertTrue(registry.dispatch(ott, self.client, self.path, list(words), json_output))
        return output.getvalue()

    def add(self):
        return self.run_command('registry', 'add', 'tv', 'device-tv')

    def test_add_confirms_then_saves_alias_without_static_credentials(self):
        output = self.add()
        self.assertIn('active', output)
        self.assertNotIn('a' * 32, output)
        self.assertEqual(ott.read_json(self.credentials), self.original_credentials)
        self.assertEqual(ott.read_json(self.path)['players'], {'tv': 'device-tv'})
        self.assertFalse(self.checkpoint.exists())
        self.assertEqual([path for path, _ in self.client.calls], ['/api/device-registry', '/api/device-registry/add', '/api/device-registry'])

    def test_lost_ack_resumes_receipt_without_another_post(self):
        self.client.fail_next = 'after'
        with self.assertRaisesRegex(ott.Error, 'unconfirmed'):
            self.add()
        self.assertEqual(ott.read_json(self.path)['players'], {})
        self.assertTrue(self.checkpoint.exists())
        self.run_command('registry', 'resume')
        self.assertEqual(sum(payload is not None for _, payload in self.client.calls), 1)
        self.assertEqual(ott.read_json(self.path)['players'], {'tv': 'device-tv'})

    def test_unknown_pairing_approval_is_durable_before_alias_and_resumes_once(self):
        self.client.pairings = [{'id': 'd' * 32, 'device_id': 'new-tv', 'code': 'ABCDEFGH',
                                'address': 'https://player.example/ott-control', 'expires_in': 600}]
        self.client.fail_next = 'after'
        with self.assertRaisesRegex(ott.Error, 'unconfirmed'):
            self.run_command('registry', 'approve', 'tv', 'new-tv', 'ABCDEFGH')
        self.assertEqual(ott.read_json(self.path)['players'], {})
        self.run_command('registry', 'resume')
        self.assertEqual(ott.read_json(self.path)['players'], {'tv': 'new-tv'})
        self.assertEqual(sum(path == '/api/pairings/approve' for path, _ in self.client.calls), 1)
        self.assertEqual(ott.read_json(self.credentials), self.original_credentials)

    def test_changed_generation_after_commit_never_saves_alias(self):
        self.client.after_write = lambda: self.client.rows['device-tv'].update(generation='e' * 32)
        with self.assertRaisesRegex(ott.Error, 'device changed afterward'):
            self.add()
        self.assertEqual(ott.read_json(self.path)['players'], {})
        self.assertTrue(self.checkpoint.exists())
        count = len(self.client.calls)
        with self.assertRaisesRegex(ott.Error, 'device changed afterward'):
            self.run_command('registry', 'resume')
        self.assertFalse(any(payload is not None for _, payload in self.client.calls[count:]))

    def test_late_final_confirmation_keeps_checkpoint_without_alias_update(self):
        clock = [100.0]
        original = self.client.api
        def delayed(path, payload=None, timeout=None):
            result = original(path, payload, timeout)
            if path == '/api/device-registry' and self.client.rows:
                clock[0] = 146.0
            return result
        with mock.patch.object(registry.time, 'monotonic', side_effect=lambda: clock[0]), mock.patch.object(self.client, 'api', side_effect=delayed):
            with self.assertRaisesRegex(ott.Error, 'timed out'):
                self.add()
        self.assertEqual(ott.read_json(self.path)['players'], {})
        self.assertTrue(self.checkpoint.exists())
        self.run_command('registry', 'resume')
        self.assertEqual(ott.read_json(self.path)['players'], {'tv': 'device-tv'})
        self.assertEqual(sum(payload is not None for _, payload in self.client.calls), 1)

    def test_pairing_approval_fetches_receipt_and_rejects_ambiguous_pending(self):
        pending = {'id': 'd' * 32, 'device_id': 'new-tv', 'code': 'ABCDEFGH',
                   'address': 'https://player.example/ott-control', 'expires_in': 600}
        self.client.pairings = [pending, {**pending, 'id': 'e' * 32}]
        with self.assertRaisesRegex(ott.Error, 'No unique pending pairing'):
            self.run_command('registry', 'approve', 'tv', 'new-tv', 'ABCDEFGH')
        self.assertFalse(any(payload is not None for _, payload in self.client.calls))
        self.client.pairings = [pending]
        self.run_command('registry', 'approve', 'tv', 'new-tv', 'ABCDEFGH')
        self.assertEqual(ott.read_json(self.path)['players'], {'tv': 'new-tv'})
        self.assertTrue(any('/operations?' in path for path, _ in self.client.calls))
        self.assertFalse(self.checkpoint.exists())

    def test_shared_contract_request_corpus(self):
        fixture = json.loads((ROOT / 'contracts/device-registry-v1.json').read_text())
        for kind in ('add', 'revoke', 'rotate', 'reconcile'):
            self.assertTrue(registry.valid_request(kind, fixture['requests'][kind]))
        self.assertTrue(registry.valid_request('add', fixture['requests']['convert_absent_static']))
        for request in fixture['invalid_add']:
            with self.subTest(request=request):
                self.assertFalse(registry.valid_request('add', request))
        for key, value in registry.LIMITS.items():
            self.assertEqual(fixture['limits'][key], value)

    def test_private_checkpoint_lock_and_symlink_reject_before_mutation(self):
        with registry.Guard(ott, self.checkpoint):
            with self.assertRaisesRegex(ott.Error, 'Another registry operation'):
                self.add()
        target = Path(self.temp.name) / 'target.json'
        ott.write_private(target, {})
        try:
            self.checkpoint.symlink_to(target)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaisesRegex(ott.Error, 'private regular files'):
            self.add()
        self.assertFalse(any(payload is not None for _, payload in self.client.calls))

    def test_explicit_resume_retries_only_same_uncommitted_operation(self):
        self.client.fail_next = 'before'
        with self.assertRaises(ott.Error):
            self.add()
        original = json.loads(self.checkpoint.read_text())['request']
        self.run_command('registry', 'resume')
        posts = [payload for _, payload in self.client.calls if payload is not None]
        self.assertEqual(posts, [original, original])

    def test_wrong_store_expired_checkpoint_and_changed_admin_never_retry(self):
        for change in ('store', 'time', 'admin'):
            with self.subTest(change=change):
                self.client.fail_next = 'before'
                with self.assertRaises(ott.Error):
                    self.add()
                state = json.loads(self.checkpoint.read_text())
                if change == 'store':
                    state['binding']['store_id'] = 'c' * 32
                elif change == 'time':
                    state['created_at'] = int(time.time()) - 86401
                else:
                    state['binding']['administrator'] = 'd' * 64
                ott.write_private(self.checkpoint, state)
                count = len(self.client.calls)
                with self.assertRaisesRegex(ott.Error, 'Checkpoint does not match'):
                    self.run_command('registry', 'resume')
                self.assertFalse(any(payload is not None for _, payload in self.client.calls[count:]))
                self.checkpoint.unlink()

    def test_concurrent_alias_change_is_preserved_after_server_commit(self):
        def change():
            updated = copy.deepcopy(self.config)
            updated['players']['tv'] = 'someone-else'
            ott.write_private(self.path, updated)
        self.client.after_write = change
        with self.assertRaisesRegex(ott.Error, 'another identity'):
            self.add()
        self.assertEqual(ott.read_json(self.path)['players']['tv'], 'someone-else')
        self.assertTrue(self.checkpoint.exists())

    def test_remove_revokes_then_unaliases_but_unalias_never_contacts_server(self):
        self.add()
        output = self.run_command('remove', 'tv')
        self.assertIn('stopping has not been confirmed', output)
        self.assertEqual(self.client.rows['device-tv']['state'], 'revoked')
        self.assertEqual(ott.read_json(self.path)['players'], {})
        config = ott.read_json(self.path)
        config['players']['other'] = 'device-tv'
        ott.write_private(self.path, config)
        count = len(self.client.calls)
        self.run_command('unalias', 'other')
        self.assertEqual(len(self.client.calls), count)

    def test_credential_is_explicit_private_and_never_overwrites(self):
        self.add()
        with self.assertRaisesRegex(ott.Error, '--output'):
            self.run_command('registry', 'credential', 'tv')
        self.assertFalse(any('/credential?' in path for path, _ in self.client.calls))
        output_path = Path(self.temp.name) / 'credential.json'
        output = self.run_command('registry', 'credential', 'tv', '--output', str(output_path))
        self.assertNotIn('z' * 43, output)
        self.assertEqual(json.loads(output_path.read_text())['token'], 'z' * 43)
        if os.name != 'nt':
            self.assertEqual(output_path.stat().st_mode & 0o777, 0o600)
        original = output_path.read_bytes()
        with self.assertRaises(ott.Error):
            self.run_command('registry', 'credential', 'tv', '--output', str(output_path))
        self.assertEqual(output_path.read_bytes(), original)
        self.assertIn('z' * 43, self.run_command('registry', 'credential', 'tv', '--show-secret'))

    def test_probe_falls_back_only_for_definitive_unsupported(self):
        for error, fallback in [(ott.HTTPError(404), True), (ott.HTTPError(403), False),
                                (ott.HTTPError(503), False), (ott.TransportError('offline'), False)]:
            with self.subTest(error=error):
                self.client.probe_error = error
                if fallback:
                    self.assertFalse(registry.dispatch(ott, self.client, self.path, ['add', 'tv', 'device-tv']))
                else:
                    with self.assertRaises(ott.Error):
                        registry.dispatch(ott, self.client, self.path, ['add', 'tv', 'device-tv'])
        self.assertEqual(ott.read_json(self.credentials), self.original_credentials)

    def test_registry_intent_or_pending_operation_disables_legacy_fallback(self):
        self.client.probe_error = ott.HTTPError(404)
        self.client.credentials['device_registry'] = {'path': '/private/registry'}
        with self.assertRaises(ott.HTTPError):
            registry.dispatch(ott, self.client, self.path, ['add', 'tv', 'device-tv'])
        del self.client.credentials['device_registry']
        ott.write_private(self.checkpoint, {})
        with self.assertRaises(ott.HTTPError):
            registry.dispatch(ott, self.client, self.path, ['add', 'tv', 'device-tv'])

    def test_strict_inventory_and_receipt_binding(self):
        for mutate in (lambda v: v.update(version=True), lambda v: v.update(token='secret'),
                       lambda v: v['limits'].update(operations=True)):
            value = self.client.snapshot()
            mutate(value)
            with self.assertRaises(ott.Error):
                registry.inventory(ott, value)
        request = {'operation_id': 'a' * 32, 'device_id': 'device-tv'}
        bad = {'version': 1, **request, 'generation': 'b' * 32, 'state': 'revoked', 'persisted': True,
               'device_stop_confirmed': True}
        with self.assertRaises(ott.Error):
            registry.receipt(ott, bad, request, 'revoke')

    def test_offline_help_and_unalias_do_not_construct_a_client(self):
        with mock.patch.object(ott, 'Client', side_effect=AssertionError('must stay offline')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ott.main(['-c', '/missing', 'registry', '--help']), 0)
            config = ott.read_json(self.path)
            config['players']['tv'] = 'device-tv'
            ott.write_private(self.path, config)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ott.main(['-c', str(self.path), 'unalias', 'tv']), 0)


if __name__ == '__main__':
    unittest.main()
