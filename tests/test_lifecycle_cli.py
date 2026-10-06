"""Typed control dispatch, platform capability projection and uncertain receipts."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_lifecycle', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class LifecycleCliTest(unittest.TestCase):
    def run_cli(self, words, response, machine=False):
        client = mock.Mock()
        client.device.return_value = 'dev_tv'
        if isinstance(response, Exception): client.call.side_effect = response
        else: client.call.return_value = response
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main((['--json'] if machine else []) + ['tv'] + words)
        return code, output.getvalue(), errors.getvalue(), client.call.call_args_list

    def deferred(self, action, field, value):
        return {field: value, 'accepted': True, 'dispatched': False, 'effect': action + '-after-ack'}

    def capabilities(self):
        return {'version': 1, 'player': {'version': '1.1.52-beta.47', 'platform': 'browser', 'runtime': 'page-123-abcd'},
                'lifecycle': ['reload_player', 'standby', 'wake'], 'input': ['up', 'ok'], 'playback': []}

    def test_lifecycle_names_keep_device_reboot_app_exit_and_reload_distinct(self):
        pairs = [(['reload'], 'reload_player'), (['reload', 'PLAYER'], 'reload_player'),
                 (['restart', 'app'], 'restart_app'), (['restart', 'application'], 'restart_app'), (['restart', 'a'], 'restart_app'),
                 (['reboot'], 'reboot_device'), (['REBOOT', 'DEVICE'], 'reboot_device'),
                 (['exit'], 'exit_app'), (['QUIT'], 'exit_app'), (['close'], 'exit_app'),
                 (['standby'], 'standby'), (['WAKE'], 'wake')]
        for words, operation in pairs:
            for machine in [False, True]:
                with self.subTest(words=words, json=machine):
                    ack = self.deferred('lifecycle', 'operation', operation)
                    code, out, err, calls = self.run_cli(words, dict(ack, secret='private'), machine)
                    self.assertEqual((code, err), (0, ''))
                    self.assertEqual(calls, [mock.call('dev_tv', 'lifecycle', {'operation': operation})])
                    self.assertNotIn('private', out)
                    if machine: self.assertEqual(json.loads(out), ack)
                    else: self.assertIn('request accepted', out); self.assertIn(operation, out)

    def test_legacy_restart_stream_and_player_preserve_wire_and_ack(self):
        for target, words in [('player', ['restart']), ('stream', ['restart', 's']), ('stream', ['restart', 'stream']),
                              ('player', ['restart', 'p']), ('player', ['restart', 'player'])]:
            ack = {'accepted': True, 'target': target, 'dispatched': target == 'stream'}
            if target == 'player': ack['effect'] = 'reload-after-ack'
            code, out, err, calls = self.run_cli(words, dict(ack, extra='private'), True)
            self.assertEqual((code, err, json.loads(out)), (0, '', ack))
            self.assertEqual(calls, [mock.call('dev_tv', 'restart', {'target': target})])

    def test_bad_lifecycle_and_input_receipts_never_claim_success_or_retry(self):
        for words, action, field, value in [(['reboot'], 'lifecycle', 'operation', 'reboot_device'),
                                            (['key', 'enter'], 'input', 'key', 'ok')]:
            valid = self.deferred(action, field, value)
            invalid = [None, [], {}, {**valid, field: 'other'}, {**valid, 'accepted': 1}, {**valid, 'accepted': False},
                       {**valid, 'dispatched': 0}, {**valid, 'dispatched': True}, {**valid, 'effect': 'reload-after-ack'}]
            for field_name in valid:
                invalid.append({k: v for k, v in valid.items() if k != field_name})
            for reply in invalid:
                for machine in [False, True]:
                    code, out, err, calls = self.run_cli(words, reply, machine)
                    self.assertEqual((code, out, len(calls)), (1, '', 1))
                    self.assertIn('do not repeat', err)

    def test_named_input_and_aliases_never_forward_numeric_or_arbitrary_keycodes(self):
        for key in ott.INPUT_KEYS:
            self.assertEqual(ott.parse_command(['input', key.upper()]), ('input', {'key': key}))
        for alias, key in ott.INPUT_ALIASES.items():
            ack = self.deferred('input', 'key', key)
            code, out, err, calls = self.run_cli(['KEY', alias.upper()], dict(ack, private='never-print'), True)
            self.assertEqual((code, err, json.loads(out)), (0, '', ack))
            self.assertEqual(calls, [mock.call('dev_tv', 'input', {'key': key})])
        for words in [['key'], ['key', '13'], ['key', 'eval'], ['key', 'ok', 'ok'], ['input', 'power']]:
            code, out, _, calls = self.run_cli(words, {})
            self.assertEqual((code, out, calls), (1, '', []))

    def test_playback_ack_projects_only_requested_operation_and_seek_position(self):
        for words, params in [(['PAUSE'], {'operation': 'pause'}), (['resume'], {'operation': 'resume'}),
                              (['seek', '12.5'], {'operation': 'seek', 'position': 12.5}),
                              (['seek', '9007199254740991'], {'operation': 'seek', 'position': 9007199254740991}),
                              (['seek', '0'], {'operation': 'seek', 'position': 0})]:
            expected = {**params, 'dispatched': True}
            for machine in [False, True]:
                code, out, err, calls = self.run_cli(words, dict(expected, url='private'), machine)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(calls, [mock.call('dev_tv', 'playback', params)])
                self.assertNotIn('private', out)
                if machine: self.assertEqual(json.loads(out), expected)
                else: self.assertIn('request dispatched', out); self.assertIn('does not confirm', out)

    def test_wrong_playback_or_seek_receipt_never_retries(self):
        valid = {'operation': 'seek', 'position': 0, 'dispatched': True}
        bad = [{}, None, [], {**valid, 'operation': 'pause'}, {**valid, 'dispatched': 1}]
        bad.extend({**valid, 'position': value} for value in [None, False, '0', -1, 1, float('nan'), float('inf'), 10**400])
        for response in bad:
            for machine in [False, True]:
                code, out, err, calls = self.run_cli(['seek', '0'], response, machine)
                self.assertEqual((code, out, len(calls)), (1, '', 1))
                self.assertIn('do not repeat', err)

    def test_adjacent_channels_dispatch_one_typed_request_without_search_or_input(self):
        for command, operation in [('prev', 'previous_channel'), ('PREVIOUS', 'previous_channel'),
                                   ('PreV', 'previous_channel'), ('next', 'next_channel'), ('NEXT', 'next_channel')]:
            for identity in ['channel-7', 7, 0]:
                channel = {'id': identity, 'number': 12, 'name': 'РЕН ТВ HD'}
                receipt = {'operation': operation, 'dispatched': True, 'channel': channel}
                for machine in [False, True]:
                    private = {**receipt, 'token': 'never-print', 'channel': dict(channel, url='never-print')}
                    code, out, err, calls = self.run_cli([command], private, machine)
                    self.assertEqual((code, err), (0, ''))
                    self.assertEqual(calls, [mock.call('dev_tv', 'playback', {'operation': operation})])
                    self.assertNotIn('never-print', out)
                    self.assertEqual(json.loads(out) if machine else out,
                                     receipt if machine else 'Channel switch requested: 12: РЕН ТВ HD\n')

    def test_signed_offsets_survive_argparse_and_dispatch_one_exact_integer_request(self):
        for command, offset in [('+15', 15), ('-15', -15), ('+1', 1), ('-1', -1),
                                ('+00015', 15), ('-00015', -15),
                                ('+9007199254740991', 9007199254740991),
                                ('-9007199254740991', -9007199254740991),
                                ('+' + '0' * 5000 + '15', 15)]:
            params = {'operation': 'step_channel', 'offset': offset}
            channel = {'id': 7, 'number': 12, 'name': 'РЕН ТВ HD'}
            receipt = dict(params, dispatched=True, channel=channel)
            for machine in [False, True]:
                with self.subTest(command=command[:30], json=machine):
                    private = dict(receipt, secret='never-print', channel=dict(channel, url='never-print'))
                    code, out, err, calls = self.run_cli([command], private, machine)
                    self.assertEqual((code, err), (0, ''))
                    self.assertEqual(calls, [mock.call('dev_tv', 'playback', params)])
                    self.assertIs(type(calls[0].args[2]['offset']), int)
                    self.assertEqual(json.loads(out) if machine else out,
                                     receipt if machine else 'Channel switch requested: 12: РЕН ТВ HD\n')

    def test_invalid_signed_offsets_fail_locally_without_search_or_mutation(self):
        for command in ['+0', '-0', '+000', '-000', '+9007199254740992', '-9007199254740992',
                        '+' + '9' * 5000, '-' + '9' * 5000, '+1.5', '-1.5', '+1e3', '-1e3',
                        '+.5', '-.5', '+', '-', '++15', '--15', '+ 15', '+１５', '-١٥',
                        '+²', '-Ⅻ', '+½', '+.²', '--Ⅻ']:
            for machine in [False, True]:
                with self.subTest(command=command[:30], json=machine):
                    code, out, err, calls = self.run_cli([command], {}, machine)
                    self.assertEqual((code, out, calls), (1, '', []))
                    self.assertIn('Channel offset must be nonzero' if command[1:].isascii() and command[1:].isdigit()
                                  else 'Use +N or -N alone', err)
        for words in [['+15', 'now'], ['-15', '15'], ['+15', '--list']]:
            code, out, err, calls = self.run_cli(words, {})
            self.assertEqual((code, out, calls), (1, '', []))
            self.assertIn('Use +N or -N alone', err)
        for name in ['+News', '-News', '+²', '-Ⅻ', '+½']:
            self.assertEqual(ott.parse_command(['play', name]), ('play', {'query': name}))

    def test_signed_offset_does_not_change_absolute_channel_or_volume_commands(self):
        response = {'dispatched': True, 'channel': {'id': 15, 'number': 15, 'name': 'News'}}
        code, _, err, calls = self.run_cli(['15'], response)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(calls, [mock.call('dev_tv', 'play', {'query': '15'})])
        for command in ['v', 'vol', 'volume']:
            for argument, offset in [('+15', 15), ('-15', -15)]:
                code, _, err, calls = self.run_cli([command, argument], {'volume': 50, 'dispatched': True})
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(calls, [mock.call('dev_tv', 'command', {'command': 'set_volume', 'volume_step': offset})])

    def test_signed_offset_receipt_must_echo_the_requested_integer_and_channel(self):
        for command, offset in [('+15', 15), ('-15', -15), ('+1', 1), ('-1', -1)]:
            valid = {'operation': 'step_channel', 'offset': offset, 'dispatched': True,
                     'channel': {'id': 'a', 'number': 1, 'name': 'Первый'}}
            invalid = [None, {}, {k: v for k, v in valid.items() if k != 'offset'},
                       {**valid, 'operation': 'next_channel'}, {**valid, 'dispatched': 1},
                       {**valid, 'dispatched': False}, {**valid, 'channel': None},
                       {**valid, 'channel': {**valid['channel'], 'number': 0}}]
            invalid.extend({**valid, 'offset': value} for value in
                           [None, True, False, str(offset), float(offset), -offset, 0, offset + 1,
                            float('nan'), float('inf'), 9007199254740992])
            for receipt in invalid:
                for machine in [False, True]:
                    code, out, err, calls = self.run_cli([command], receipt, machine)
                    self.assertEqual((code, out, len(calls)), (1, '', 1))
                    self.assertIn('do not repeat', err)

    def test_adjacent_channel_receipt_must_identify_the_dispatched_channel(self):
        channel = {'id': 'a', 'number': 1, 'name': 'Первый'}
        valid = {'operation': 'previous_channel', 'dispatched': True, 'channel': channel}
        invalid = [None, {}, {**valid, 'operation': 'next_channel'}, {**valid, 'dispatched': 1},
                   {**valid, 'dispatched': False}, {**valid, 'channel': None}, {**valid, 'channel': []}]
        for field, values in {'id': [None, True, [], '', 'a' * 513, 9007199254740992],
                              'number': [None, True, 0, -1, 1.5, '1', 9007199254740992],
                              'name': [None, [], '', ' ', 'x' * 16385]}.items():
            invalid.extend({**valid, 'channel': {**channel, field: value}} for value in values)
        for receipt in invalid:
            for machine in [False, True]:
                code, out, err, calls = self.run_cli(['prev'], receipt, machine)
                self.assertEqual((code, out, len(calls)), (1, '', 1))
                self.assertIn('do not repeat', err)
        code, out, err, _ = self.run_cli(['prev'], {**valid, 'channel': {**channel, 'name': '\x1b[31mName'}})
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn('\x1b', out)

    def test_adjacent_arguments_are_local_errors_and_reserved_names_have_play_escape(self):
        for command in ['prev', 'previous', 'next', 'PREV']:
            for tail in [['1'], ['--list'], ['channel'], ['now', 'please']]:
                code, out, _, calls = self.run_cli([command] + tail, {})
                self.assertEqual((code, out, calls), (1, '', []))
            self.assertEqual(ott.parse_command(['play', command]), ('play', {'query': command}))
        for text in ['pre', 'nex', 'previous_channel']:
            self.assertEqual(ott.parse_command([text]), ('play', {'query': text}))

    def test_capabilities_are_bounded_public_metadata_and_allow_empty_state_dependent_lists(self):
        for command in ['caps', 'CAPABILITIES']:
            for platform in ['browser', 'future_native-platform']:
                reply = self.capabilities(); reply['player']['platform'] = platform
                safe = copy.deepcopy(reply); reply.update(token='private'); reply['player']['credential'] = 'private'
                code, out, err, calls = self.run_cli([command], reply, True)
                self.assertEqual((code, err, json.loads(out)), (0, '', safe))
                self.assertNotIn('private', out)
                self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {})])
        reply = self.capabilities(); reply.update(lifecycle=[], input=[], playback=[])
        self.assertEqual(ott.capabilities_metadata(reply), reply)

    def test_malformed_capabilities_do_not_pass_through_private_or_unbounded_fields(self):
        invalid = [None, [], {}, {**self.capabilities(), 'version': True}, {**self.capabilities(), 'version': 2}]
        for field in ['version', 'platform', 'runtime']:
            for value in [None, '', 'a' * 65, 'private\n', 'https://private', '\ud800', True]:
                data = self.capabilities(); data['player'][field] = value; invalid.append(data)
        for field in ['lifecycle', 'input', 'playback']:
            for value in [None, {}, ['unknown'], [True], [[]], ['up', 'up'], ['reload_player'] * 50]:
                data = self.capabilities(); data[field] = value; invalid.append(data)
        for reply in invalid:
            code, out, err, calls = self.run_cli(['caps'], reply, True)
            self.assertEqual((code, out, len(calls)), (1, '', 1))
            self.assertIn('invalid capabilities', err)

    def test_unsupported_rejected_and_lost_control_replies_have_no_legacy_fallback(self):
        for words in [['caps'], ['exit'], ['reboot'], ['key', 'ok'], ['pause'], ['seek', '1'], ['prev'], ['previous'], ['next'], ['+15'], ['-15']]:
            for error in [ott.PlayerUnsupported('private-player-detail'), ott.PlayerRejected('private-player-detail', {}),
                          ott.TransportError('Lost reply; do not repeat blindly')]:
                code, out, err, calls = self.run_cli(words, error, True)
                self.assertEqual((code, out, len(calls)), (1, '', 1))
                self.assertNotIn('private-player-detail', err)
                expected = {'caps': 'capabilities', 'exit': 'lifecycle', 'reboot': 'lifecycle',
                            'key': 'input', 'pause': 'playback', 'seek': 'playback',
                            'prev': 'playback', 'previous': 'playback', 'next': 'playback',
                            '+15': 'playback', '-15': 'playback'}[words[0]]
                self.assertEqual(calls[0].args[1], expected)

    def test_malformed_controls_are_rejected_locally_and_reserved_names_can_be_played(self):
        invalid = [['caps', 'extra'], ['reboot', 'player'], ['reboot', 'device', 'now'], ['reload', 'device'],
                   ['exit', 'now'], ['quit', 'now'], ['close', 'now'], ['standby', 'on'], ['wake', 'now'],
                   ['restart', 'device'], ['restart', 'app', 'now'], ['pause', '1'], ['resume', '1'], ['seek'],
                   ['seek', '1', '2'], ['seek', '-1'], ['seek', '+1'], ['seek', 'NaN'], ['seek', 'inf'], ['seek', '1e999'],
                   ['seek', '9007199254740992'], ['seek', '9' * 500]]
        for words in invalid:
            code, out, _, calls = self.run_cli(words, {})
            self.assertEqual((code, out, calls), (1, '', []))
        for name in ['caps', 'capabilities', 'key', 'input', 'pause', 'resume', 'seek', 'restart', 'reboot', 'reload', 'exit', 'quit', 'close', 'standby', 'wake']:
            self.assertEqual(ott.parse_command(['play', name]), ('play', {'query': name}))
        for name in ['reb', 'rebo', 'res', 'sta', 'qui', 'clo']:
            self.assertEqual(ott.parse_command([name]), ('play', {'query': name}))


if __name__ == '__main__':
    unittest.main()
