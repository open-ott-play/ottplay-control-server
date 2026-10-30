"""Relative seek uses a separately negotiated, single atomic playback request."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_relative_seek', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


def caps(marker=True):
    data = {'version': 1, 'player': {'version': 'test', 'platform': 'browser', 'runtime': 'page-1'},
            'lifecycle': [], 'input': [], 'playback': ['seek']}
    if marker:
        data['playback_seek_relative'] = {'version': 1}
    return data


def receipt(offset=10, start=30, position=40, clamped=False):
    return {'operation': 'seek_relative', 'dispatched': True, 'offset': offset,
            'from': start, 'position': position, 'clamped': clamped}


class RelativeSeekTest(unittest.TestCase):
    def run_cli(self, words, responses=None, selector='tv', machine=True):
        client = mock.Mock()
        client.timeout = 45
        client.config = {'players': {'tv': 'dev_tv', 'other': 'dev_other'}}
        client.credentials = {'devices': []}
        client.device.side_effect = lambda name: client.config['players'][name]
        client.call.side_effect = responses or [caps(), receipt()]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value=client.config), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['-j'] if machine else []) + [selector] + words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client

    def test_explicit_signed_and_unsigned_time_syntax(self):
        for text, value in [('10', 10), ('1m30s', 90), ('01:30', 90), ('1:02:03.5', 3723.5),
                            ('.25', .25), ('1.', 1), ('1.5h30m', 7200), ('1H2M3S', 3723),
                            ('9007199254740991', 9007199254740991), ('0', 0)]:
            with self.subTest(text=text):
                self.assertEqual(ott.parse_command(['seek', text]), ('playback', {'operation': 'seek', 'position': value}))
                if value:
                    for sign in ('+', '-'):
                        expected = value if sign == '+' else -value
                        self.assertEqual(ott.parse_command(['seek', sign + text]), ('playback', {'operation': 'seek_relative', 'offset': expected}))

    def test_unit_shorthand_preserves_channel_offsets_and_signed_names(self):
        for text, value in [('+10s', 10), ('-1m', -60), ('+1h30m', 5400), ('-.5s', -.5)]:
            self.assertEqual(ott.parse_command([text]), ('playback', {'operation': 'seek_relative', 'offset': value}))
        for text, value in [('+10', 10), ('-10', -10), ('+00010', 10)]:
            self.assertEqual(ott.parse_command([text]), ('playback', {'operation': 'step_channel', 'offset': value}))
        self.assertEqual(ott.parse_command(['+News']), ('play', {'query': '+News'}))
        self.assertEqual(ott.parse_command(['play', '-1m']), ('play', {'query': '-1m'}))

    def test_invalid_times_refuse_before_network(self):
        bad = ['', '+0', '-0s', '1e2', '+1e2', 'NaN', 'inf', '1m2h', '1m2m', '1s2m',
               '1:60', '1:00:60', '1:2', '1::30', '1.5:30', '1:00junk', '1m 2s',
               '--1s', '++1s', '1ms', '1d', '１s', '9007199254740991.01', '9' * 500,
               '+9007199254740992', '.s', '1h-2m']
        for text in bad:
            with self.subTest(text=text):
                code, out, _, client = self.run_cli(['seek', text])
                self.assertEqual((code, out, client.call.call_count), (1, '', 0))
        for words in [['+1:30'], ['+1s', 'extra'], ['seek'], ['seek', '+1', 's']]:
            code, _, _, client = self.run_cli(words)
            self.assertEqual((code, client.call.call_count), (1, 0))

    def test_parser_does_not_round_out_of_range_fraction_into_range(self):
        for text in ['9007199254740991.0000000000000001', '+2501999792984h']:
            with self.assertRaises(ott.Error): ott.parse_command(['seek', text])
        tiny = '+.' + '0' * 125 + '1'
        self.assertEqual(len(tiny), 128)
        self.assertGreater(ott.parse_command(['seek', tiny])[1]['offset'], 0)

    def test_negative_argument_and_single_atomic_request(self):
        for words in [['seek', '-10s'], ['-10s']]:
            for machine in (True, False):
                code, out, err, client = self.run_cli(words, [caps(), receipt(-10, 30, 20)], machine=machine)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {}), mock.call('dev_tv', 'playback', {'operation': 'seek_relative', 'offset': -10})])
                if machine: self.assertEqual(json.loads(out), receipt(-10, 30, 20))
                else: self.assertIn('30s → 20s (-10s)', out)

    def test_unsigned_seek_retains_one_legacy_absolute_rpc(self):
        ack = {'operation': 'seek', 'dispatched': True, 'position': 90}
        code, _, err, client = self.run_cli(['seek', '1m30s'], [ack])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'playback', {'operation': 'seek', 'position': 90})])

    def test_absent_or_bad_capability_never_sends_seek(self):
        for capability in [caps(False), dict(caps(), playback_seek_relative=None),
                           dict(caps(), playback_seek_relative={'version': True}),
                           dict(caps(), playback_seek_relative={'version': 2}),
                           dict(caps(), playback_seek_relative={'version': 1, 'secret': 'hidden'})]:
            code, out, err, client = self.run_cli(['seek', '+10'], [capability])
            self.assertEqual((code, out), (1, ''))
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {})])
            self.assertNotIn('hidden', err)
        self.assertNotIn('seek_relative', ott.PLAYBACK_OPERATIONS)
        self.assertEqual(ott.capabilities_metadata(caps())['playback'], ['seek'])

    def test_capability_rejection_reports_no_seek_sent(self):
        for response in [ott.PlayerRejected('private-token', {'error': 'private-token'}),
                         ott.PlayerUnsupported('private-token')]:
            code, out, err, client = self.run_cli(['seek', '+10'], [response])
            self.assertEqual((code, out), (1, ''))
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {})])
            self.assertIn('No seek was sent', err)
            self.assertNotIn('The request was not repeated', err)
            self.assertNotIn('private-token', err)

    def test_rejection_timeout_and_bad_receipt_never_replay(self):
        for response in [ott.PlayerRejected('private-token', {'error': 'private-token'}), ott.PlayerUnsupported('private-token'),
                         ott.Error('request timed out'), receipt(position=41),
                         dict(receipt(), secret='private-token'), dict(receipt(), offset=True),
                         dict(receipt(), clamped=1), dict(receipt(), **{'from': float('nan')}),
                         dict(receipt(), **{'from': 10**400}), dict(receipt(), position=float('inf'))]:
            code, out, err, client = self.run_cli(['seek', '+10'], [caps(), response])
            self.assertEqual((code, out), (1, ''))
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {}),
                mock.call('dev_tv', 'playback', {'operation': 'seek_relative', 'offset': 10})])
            self.assertNotIn('private-token', err)
            if isinstance(response, (ott.PlayerRejected, ott.PlayerUnsupported)):
                self.assertIn('The request was not repeated', err)
                self.assertNotIn('No seek was sent', err)

    def test_clamp_receipts_checked_without_inventing_duration(self):
        # At exact duration a tiny negative offset can still hit the end guard.
        # Duration is not in this dispatch receipt; it is not decoder-position proof.
        for ack in [receipt(-40, 30, 0, True), receipt(10, 95, 99.999, True),
                    receipt(-.0001, 90, 89.999, True)]:
            code, out, err, _ = self.run_cli(['seek', str(ack['offset']) if ack['offset'] < 0 else '+10'], [caps(), ack])
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(json.loads(out), ack)
        for ack in [receipt(10, 30, 40, True), receipt(-40, 30, 1, True), receipt(10, 30, -1, True)]:
            code, _, _, client = self.run_cli(['seek', f"{ack['offset']:+g}"], [caps(), ack])
            self.assertEqual((code, client.call.call_count), (1, 2))
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {}),
                mock.call('dev_tv', 'playback', {'operation': 'seek_relative', 'offset': ack['offset']})])

    def test_whole_deadline_no_second_request_when_discovery_expires(self):
        client = mock.Mock(timeout=45)
        client.call.return_value = caps()
        with mock.patch.object(ott.time, 'monotonic', side_effect=[100, 146]):
            with self.assertRaisesRegex(ott.Error, 'no seek was sent'):
                ott.relative_seek_command(client, 'dev', {'operation': 'seek_relative', 'offset': 10}, 45)
        self.assertEqual(client.call.call_count, 1)
        self.assertEqual(client.timeout, 45)

    def test_actual_transport_lost_receipt_retains_identity_without_reposting(self):
        client = object.__new__(ott.Client)
        client.timeout = 45
        posts, receipts, now = [], [], [0]
        client.on_receipt = receipts.append

        def api(path, payload=None, timeout=None):
            if payload is not None:
                posts.append(payload)
                return 202, {'id': f'{len(posts):032x}'}
            if path.endswith(f'id={1:032x}'):
                return 200, {'id': f'{1:032x}', 'status': 'ok', 'data': caps()}
            now[0] += timeout
            raise ott.HTTPError(503)

        client.api = api
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]), \
                mock.patch.object(ott.time, 'sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)):
            with self.assertRaises(ott.RequestError) as failure:
                ott.relative_seek_command(client, 'dev', {'operation': 'seek_relative', 'offset': 10}, 5)
        self.assertEqual(posts, [{'action': 'capabilities', 'params': {}},
                                 {'action': 'playback', 'params': {'operation': 'seek_relative', 'offset': 10}}])
        self.assertEqual((failure.exception.request_id, failure.exception.action), (f'{2:032x}', 'playback'))
        self.assertEqual(receipts[-1], {'action': 'playback', 'request_id': f'{2:032x}', 'status': 'unknown'})
        self.assertEqual(client.timeout, 45)
        self.assertLessEqual(now[0], 5)

    def test_groups_resolve_each_player_once_without_fallback(self):
        def call(device, action, params):
            return caps(device == 'dev_tv') if action == 'capabilities' else receipt()
        code, out, _, client = self.run_cli(['seek', '+10'], call, selector='tv,other,tv')
        self.assertEqual(code, 1)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {}), mock.call('dev_tv', 'playback', {'operation': 'seek_relative', 'offset': 10}), mock.call('dev_other', 'capabilities', {})])
        self.assertIn('seek_relative', out)

    def test_capability_help_is_separate(self):
        for supported in (True, False):
            out = io.StringIO()
            capability = dict(caps(supported), playback=[])
            with contextlib.redirect_stdout(out):
                ott.print_player_help({'capabilities': ott.capabilities_metadata(capability)}, 'tv')
            self.assertEqual('seek +10s' in out.getvalue(), supported)
            self.assertEqual('No controls are currently advertised' in out.getvalue(), not supported)


if __name__ == '__main__': unittest.main()
