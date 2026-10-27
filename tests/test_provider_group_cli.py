"""Comma-separated provider changes preserve per-player outcomes and identity."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_provider_group', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class ProviderGroupTest(unittest.TestCase):
    def make_client(self, aliases=None):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': aliases or {'t1': 'dev_1', 't2': 'dev_2', 't3': 'dev_3', 't4': 'dev_4'}}
        client.credentials = {'devices': [{'id': value} for value in dict.fromkeys(client.config['players'].values())]}
        client.timeout = 1
        client.call = mock.Mock(return_value={'dispatched': True, 'provider': 'm3u'})
        return client

    def run_cli(self, words, client=None):
        client = client or self.make_client()
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value=client.config), \
                mock.patch.object(ott, 'Client', return_value=client), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(words)
        return code, output.getvalue(), errors.getvalue(), client

    def test_four_players_use_single_provider_requests_in_requested_order(self):
        for verb in ('provider', 'prov', 'PROV'):
            with self.subTest(verb=verb):
                code, out, err, client = self.run_cli(['t1,t2,t3,t4', verb, 'm3u'])
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(out, ''.join(f't{i}: Provider switch requested: m3u\n' for i in range(1, 5)))
                self.assertEqual(client.call.call_args_list,
                    [mock.call(f'dev_{i}', 'provider', {'query': 'm3u'}) for i in range(1, 5)])

    def test_every_target_is_resolved_before_the_first_mutation(self):
        client = self.make_client()
        events = []
        resolve = client.device
        client.device = lambda name: (events.append(('resolve', name)), resolve(name))[1]
        client.call.side_effect = lambda device, *args: (events.append(('send', device)),
            {'dispatched': True, 'provider': 'm3u'})[1]
        code, _, _, _ = self.run_cli(['t1,t2,t3,t4', 'provider', 'm3u'], client)
        self.assertEqual(code, 0)
        self.assertEqual(events[:4], [('resolve', f't{i}') for i in range(1, 5)])
        self.assertEqual(events[4:], [('send', f'dev_{i}') for i in range(1, 5)])

    def test_invalid_or_unknown_lists_send_nothing(self):
        for selector in ('t1,', ',t1', 't1,,t2', 't1, ,t2', ',', 't1,missing,t2'):
            with self.subTest(selector=selector):
                code, out, err, client = self.run_cli([selector, 'provider', 'm3u'])
                self.assertEqual((code, out), (1, ''))
                self.assertTrue(err.startswith('Error: '))
                client.call.assert_not_called()
        for words in (['provider'], ['prov', '   '], ['provider-config', 'secret.json']):
            with self.subTest(words=words):
                code, out, _, client = self.run_cli(['t1,t2'] + words)
                self.assertEqual((code, out), (1, ''))
                client.call.assert_not_called()

    def test_aliases_and_uuid_deduplicate_exact_device_ids(self):
        client = self.make_client({'t1': 'dev_1', 'living': 'dev_1', 't2': 'dev_2', 'upper': 'DEV_1'})
        client.call.side_effect = lambda device, action, params: (
            {'providers': [{'id': 'm3u', 'index': 0, 'name': 'Мой провайдер'}]} if action == 'providers'
            else {'dispatched': True, 'provider': 'm3u'})
        code, out, err, _ = self.run_cli(['--json', 'T1, living ,DEV_2,t2,upper', 'prov', 'Мой', 'провайдер'], client)
        self.assertEqual((code, err), (0, ''))
        result = json.loads(out)
        self.assertEqual([row['player'] for row in result['results']], ['T1', 'DEV_2', 'upper'])
        self.assertEqual(client.call.call_args_list, [request for device in ('dev_1', 'dev_2', 'DEV_1')
            for request in (mock.call(device, 'providers', {}), mock.call(device, 'provider', {'query': 'Мой провайдер'}))])
        self.assertEqual((result['succeeded'], result['failed']), (3, 0))

    def test_single_target_and_exact_comma_alias_keep_existing_output(self):
        response = {'dispatched': True, 'provider': 'm3u', 'legacy_extension': 1}
        for selector, aliases in [('t1', {'t1': 'dev_1'}), ('T1,T2', {'t1,t2': 'group_device', 't1': 'dev_1', 't2': 'dev_2'})]:
            for verb in ('provider', 'prov'):
                with self.subTest(selector=selector, verb=verb):
                    client = self.make_client(aliases)
                    client.call.return_value = response
                    code, out, err, _ = self.run_cli([selector, verb, 'm3u'], client)
                    self.assertEqual((code, err, json.loads(out)), (0, '', response))
                    client.call.assert_called_once_with(aliases[selector.casefold()], 'provider', {'query': 'm3u'})

    def test_exact_comma_uuid_keeps_existing_single_target_behavior(self):
        client = self.make_client({'legacy': 'old,uuid', 't1': 'dev_1'})
        code, out, err, _ = self.run_cli(['old,uuid', 'provider', 'm3u'], client)
        self.assertEqual((code, err, json.loads(out)), (0, '', {'dispatched': True, 'provider': 'm3u'}))
        client.call.assert_called_once_with('old,uuid', 'provider', {'query': 'm3u'})

    def test_ambiguous_exact_comma_alias_does_not_fall_back_to_fanout(self):
        client = self.make_client({'t1,t2': 'legacy', 'T1,T2': 'other', 't1': 'dev_1', 't2': 'dev_2'})
        code, out, _, _ = self.run_cli(['t1,t2', 'provider', 'm3u'], client)
        self.assertEqual((code, out), (1, ''))
        client.call.assert_not_called()

    def test_mixed_failure_json_is_one_complete_ordered_result(self):
        client = self.make_client()
        client.call.side_effect = [
            {'dispatched': True, 'provider': 'm3u', 'secret': 'do-not-show'},
            ott.PlayerRejected('https://private/?token=do-not-show', {'error': 'do-not-show'}),
            ott.Error('The request may have been accepted; do not repeat the change blindly.'),
            {'dispatched': True, 'provider': 'm3u'},
        ]
        code, out, err, _ = self.run_cli(['-j', 't1,t2,t3,t4', 'provider', 'm3u'], client)
        self.assertEqual((code, err), (1, ''))
        result = json.loads(out)
        self.assertEqual(set(result), {'action', 'results', 'succeeded', 'failed'})
        self.assertEqual((result['action'], result['succeeded'], result['failed']), ('provider', 2, 2))
        self.assertEqual([r['player'] for r in result['results']], ['t1', 't2', 't3', 't4'])
        self.assertEqual([r['ok'] for r in result['results']], [True, False, False, True])
        self.assertIn('rejected', result['results'][1]['error'])
        self.assertIn('may have been accepted', result['results'][2]['error'])
        self.assertNotIn('do-not-show', out + err)
        self.assertEqual(client.call.call_count, 4)

    def test_invalid_ack_is_failure_and_does_not_stop_later_target(self):
        for data in ({}, [], None, {'dispatched': 1, 'provider': 'm3u'},
                     {'dispatched': True, 'provider': 'https://secret'}, {'dispatched': True, 'provider': ''}):
            with self.subTest(data=data):
                client = self.make_client()
                client.call.side_effect = [data, {'dispatched': True, 'provider': 'm3u'}]
                code, out, _, _ = self.run_cli(['-j', 't1,t2', 'provider', 'm3u'], client)
                result = json.loads(out)
                self.assertEqual(code, 1)
                self.assertIn('may have executed', result['results'][0]['error'])
                self.assertTrue(result['results'][1]['ok'])
                self.assertNotIn('https://secret', out)
                self.assertEqual(client.call.call_count, 2)

    def test_text_result_is_emitted_before_next_player_starts(self):
        client = self.make_client()
        output, errors = io.StringIO(), io.StringIO()
        events = []
        def call(device, action, params):
            events.append(device)
            if device == 'dev_2':
                self.assertEqual(output.getvalue(), 't1: Provider switch requested: m3u\n')
                raise ott.PlayerUnsupported('private', {})
            return {'dispatched': True, 'provider': 'm3u'}
        client.call.side_effect = call
        with mock.patch.object(ott, 'read_json', return_value=client.config), \
                mock.patch.object(ott, 'Client', return_value=client), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(['t1,t2,t3', 'provider', 'm3u'])
        self.assertEqual(code, 1)
        self.assertEqual(events, ['dev_1', 'dev_2', 'dev_3'])
        self.assertIn('t2: Error: The provider request was unsupported', output.getvalue())
        self.assertTrue(output.getvalue().endswith('t3: Provider switch requested: m3u\n'))

    def test_real_client_receipts_bind_each_target_and_never_repeat_posts(self):
        client = self.make_client()
        del client.call  # Exercise Client.call, call_receipt and _wait_receipt.
        posted = []
        responses = {}
        def api(path, payload=None, timeout=None):
            device = ott.urllib.parse.parse_qs(ott.urllib.parse.urlsplit(path).query)['device_id'][0]
            if payload is not None:
                posted.append(device)
                self.assertEqual(payload, {'action': 'provider', 'params': {'query': 'm3u'}})
                if device == 'dev_2':
                    raise ott.TransportError('Connection interrupted')
                request_id = f'{len(posted):032x}'
                responses[device] = request_id
                return 202, {'id': request_id}
            request_id = responses[device]
            if device == 'dev_3':
                return 200, {'id': request_id, 'status': 'unsupported', 'data': {'error': 'secret-token'}}
            return 200, {'id': request_id, 'status': 'ok', 'data': {'dispatched': True, 'provider': 'm3u'}}
        client.api = mock.Mock(side_effect=api)
        with mock.patch.object(ott.time, 'sleep'):
            code, out, err, _ = self.run_cli(['-t', '1', '-j', '--receipt', 't1,t2,t3,t4', 'prov', 'm3u'], client)
        result = json.loads(out)
        receipts = [json.loads(line) for line in err.splitlines()]
        self.assertEqual(code, 1)
        self.assertEqual(posted, ['dev_1', 'dev_2', 'dev_3', 'dev_4'])
        self.assertEqual([r['player'] for r in receipts], ['t1', 't2', 't3', 't4'])
        self.assertEqual([r['status'] for r in receipts], ['ok', 'unknown', 'unsupported', 'ok'])
        self.assertEqual([r['request_id'] for r in receipts], [f'{1:032x}', None, f'{3:032x}', f'{4:032x}'])
        self.assertTrue(all(r['action'] == 'provider' for r in receipts))
        self.assertIn('may have been accepted', result['results'][1]['error'])
        self.assertNotIn('secret-token', out + err)
        self.assertIs(client.on_receipt, ott.print_request_receipt)


if __name__ == '__main__':
    unittest.main()
