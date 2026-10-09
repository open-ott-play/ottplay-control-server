"""Runtime-bound aspect controls preserve capability, ACK and no-replay boundaries."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_aspect', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

SECRET = 'private-token-never-print'
RUNTIME = 'page-123'


def caps(operations=None, modes=None):
    return {'version': 1, 'player': {'version': '1.1.54', 'platform': 'webos', 'runtime': RUNTIME},
            'lifecycle': [], 'input': [], 'playback': [],
            'aspect': {'version': 1, 'operations': ['get', 'set'] if operations is None else operations,
                       'modes': ['fit', 'fill'] if modes is None else modes}}


def current(mode='fit', saved_mode='fit'):
    return {'version': 1, 'runtime': RUNTIME, 'operation': 'get', 'mode': mode,
            'saved_mode': saved_mode, 'persisted': saved_mode is not None and saved_mode == mode}


def accepted(mode='fit'):
    return {'version': 1, 'runtime': RUNTIME, 'operation': 'set', 'mode': mode,
            'accepted': True, 'dispatched': False, 'effect': 'aspect-after-ack'}


class AspectCliTest(unittest.TestCase):
    def run_cli(self, words, responses, machine=False, name='tv'):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = responses
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = ott.main((['--json'] if machine else []) + [name] + words)
        self.assertEqual(client.timeout, 45)
        return result, output.getvalue(), errors.getvalue(), client.call.call_args_list

    def test_read_aliases_send_one_runtime_bound_get_after_capabilities(self):
        for command in ['aspect', 'ASPECT', 'AsPeCt-RaTiO']:
            for tail in [[], ['get'], ['GET'], ['status'], ['StAtUs']]:
                for machine in [False, True]:
                    with self.subTest(command=command, tail=tail, machine=machine):
                        result = current()
                        code, out, err, calls = self.run_cli([command] + tail, [caps(), result], machine)
                        self.assertEqual((code, err), (0, ''))
                        self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                                mock.call('dev_tv', 'aspect', {'operation': 'get', 'runtime': RUNTIME})])
                        if machine:
                            self.assertEqual(json.loads(out), result)
                        else:
                            self.assertEqual(out, 'Aspect ratio: Fit to screen (fit).\nSaved mode: Fit to screen (fit).\n')

    def test_set_aliases_send_exact_mode_without_keypress_fallback_or_followup_read(self):
        for command in ['aspect', 'ASPECT-RATIO']:
            for tail, mode in [(['fit'], 'fit'), (['FILL'], 'fill'), (['Fit to screen'], 'fit'),
                               (['FIT', 'to', 'sCrEeN'], 'fit'), (['Fill screen'], 'fill'),
                               (['fIlL', 'SCREEN'], 'fill')]:
                for machine in [False, True]:
                    result = accepted(mode)
                    code, out, err, calls = self.run_cli([command] + tail, [caps(), result], machine)
                    self.assertEqual((code, err), (0, ''))
                    self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                            mock.call('dev_tv', 'aspect', {'operation': 'set', 'mode': mode, 'runtime': RUNTIME})])
                    if machine:
                        self.assertEqual(json.loads(out), result)
                    else:
                        self.assertIn('request accepted: ' + ott.ASPECT_MODES[mode], out)
                        self.assertIn('Application and persistence are not yet confirmed', out)
                        self.assertIn('Run ott tv aspect', out)
                        self.assertNotIn('mode saved', out)
                        self.assertNotIn('mode applied', out)

    def test_set_readback_hint_quotes_the_player_name(self):
        code, out, err, _ = self.run_cli(['aspect', 'fill'], [caps(), accepted('fill')], name='Living room')
        self.assertEqual((code, err), (0, ''))
        self.assertIn("Run ott 'Living room' aspect", out)

    def test_invalid_syntax_fails_locally_and_preserves_explicit_channel_escape(self):
        invalid = [['set'], ['set', 'fit'], ['get', 'fit'], ['status', 'now'], ['fill', 'fit'],
                   ['fit', 'screen'], ['fill', 'to', 'screen'], ['Fit  to screen'], ['fit\nscreen'],
                   ['auto'], ['16:9'], ['0'], ['fit*'], [SECRET], ['--list']]
        for tail in invalid:
            code, out, err, calls = self.run_cli(['aspect'] + tail, [])
            self.assertEqual((code, out, calls), (1, '', []))
            self.assertIn('Use aspect', err)
            self.assertNotIn(SECRET, err)
        for name in ['aspect', 'ASPECT', 'aspect-ratio', 'Fit to screen', 'Fill screen']:
            self.assertEqual(ott.parse_command(['play', name]), ('play', {'query': name}))
        for name in ['asp', 'aspect-r', 'aspectual', 'fit', 'fill']:
            self.assertEqual(ott.parse_command([name]), ('play', {'query': name}))

    def test_existing_named_aspect_input_and_settings_keep_their_paths(self):
        result = {'key': 'aspect', 'accepted': True, 'dispatched': False, 'effect': 'input-after-ack'}
        code, out, err, calls = self.run_cli(['key', 'aspect'], [result], True)
        self.assertEqual((code, err, json.loads(out)), (0, '', result))
        self.assertEqual(calls, [mock.call('dev_tv', 'input', {'key': 'aspect'})])
        self.assertEqual(ott.parse_command(['profile', '1', 'name', 'Fit to screen']),
                         ('profile_settings', {'number': 1, 'settings': {'name': 'Fit to screen'}}))
        self.assertEqual(ott.parse_command(['provider', 'aspect']), ('provider', {'query': 'aspect'}))
        self.assertEqual(ott.parse_command(['s', 'aspect']), ('channels', {'search': 'aspect'}))

    def test_missing_explicitly_unsupported_or_restricted_capabilities_never_send_set(self):
        old = caps(); old.pop('aspect')
        unsupported = caps([], [])
        read_only = caps(['get'])
        only_fill = caps(modes=['fill'])
        for capability in [old, unsupported, read_only, only_fill]:
            code, out, err, calls = self.run_cli(['aspect', 'fit'], [capability])
            self.assertEqual((code, out, len(calls)), (1, '', 1))
            self.assertIn('No aspect command was sent', err)
            self.assertIn('not supported', err)
        code, out, err, calls = self.run_cli(['aspect'], [caps(['set'])])
        self.assertEqual((code, out, len(calls)), (1, '', 1))
        self.assertIn('No aspect command was sent', err)

    def test_capability_subsets_allow_only_the_requested_operation_and_mode(self):
        for capability in [caps(['get']), caps(['get'], ['fit'])]:
            code, out, err, calls = self.run_cli(['aspect'], [capability, current()], True)
            self.assertEqual((code, err, len(calls)), (0, '', 2))
            self.assertEqual(json.loads(out), current())
        code, out, err, calls = self.run_cli(['aspect', 'fill'], [caps(['set'], ['fill']), accepted('fill')], True)
        self.assertEqual((code, err, len(calls)), (0, '', 2))
        self.assertEqual(json.loads(out), accepted('fill'))

    def test_malformed_aspect_capabilities_never_mutate_or_expose_unknown_fields(self):
        values = [None, [], {}, {'version': True, 'operations': ['get'], 'modes': ['fit']},
                  {'version': 2, 'operations': ['get'], 'modes': ['fit']},
                  {'version': 1, 'operations': ['get'], 'modes': ['fit'], 'private': SECRET}]
        for field, invalid in [('operations', [None, 'get', {}, ['get', 'get'], ['status'], [True], [[]],
                                                ['get', 'set', 'get'], []]),
                               ('modes', [None, 'fit', {}, ['fit', 'fit'], ['auto'], [True], [[]],
                                          ['fit', 'fill', 'fit'], []])]:
            for value in invalid:
                aspect = caps()['aspect']; aspect[field] = value; values.append(aspect)
        for field in ['version', 'operations', 'modes']:
            aspect = caps()['aspect']; del aspect[field]; values.append(aspect)
        for value in values:
            capability = caps(); capability['aspect'] = value
            with self.subTest(aspect=value):
                code, out, err, calls = self.run_cli(['aspect', 'fit'], [capability])
                self.assertEqual((code, out, len(calls)), (1, '', 1))
                self.assertIn('invalid capabilities', err)
                self.assertNotIn(SECRET, err)
        capability = caps(); capability['player']['runtime'] = SECRET + '\n'
        self.assertEqual(len(self.run_cli(['aspect', 'fit'], [capability])[3]), 1)

    def test_caps_projection_and_status_listing_follow_negotiated_controls(self):
        for operations, modes in [(['get', 'set'], ['fit', 'fill']), (['get'], ['fill']),
                                  (['set'], ['fit']), ([], [])]:
            capability = caps(operations, modes)
            private = copy.deepcopy(capability); private['secret'] = SECRET
            code, out, err, calls = self.run_cli(['caps'], [private], True)
            self.assertEqual((code, err, len(calls)), (0, '', 1))
            self.assertEqual(json.loads(out), capability)
            self.assertNotIn(SECRET, out)
            code, out, err, calls = self.run_cli([], [{'ready': True}, capability], name='Living room')
            self.assertEqual((code, err, len(calls)), (0, '', 2))
            self.assertEqual("ott 'Living room' aspect  — show" in out, 'get' in operations)
            self.assertEqual('— request an aspect mode' in out, 'set' in operations)
            if 'set' in operations:
                self.assertIn("ott 'Living room' aspect " + ' / '.join(modes), out)
            self.assertEqual('No controls are currently advertised' in out, not operations)
        code, out, err, _ = self.run_cli([], [{'ready': True}, {k: v for k, v in caps().items() if k != 'aspect'}])
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn('ott tv aspect', out)

    def test_get_reports_current_and_backing_saved_modes_without_inference(self):
        for mode in ['fit', 'fill']:
            for saved in ['fit', 'fill', None]:
                result = current(mode, saved)
                for machine in [False, True]:
                    code, out, err, calls = self.run_cli(['aspect'], [caps(), result], machine)
                    self.assertEqual((code, err, len(calls)), (0, '', 2))
                    if machine:
                        self.assertEqual(json.loads(out), result)
                    else:
                        self.assertIn('Aspect ratio: ' + ott.ASPECT_MODES[mode], out)
                        self.assertIn('Saved mode: ' + ('unavailable' if saved is None else ott.ASPECT_MODES[saved]), out)

    def test_invalid_get_metadata_never_claims_a_current_or_saved_mode(self):
        valid = current()
        invalid = [None, [], {}, dict(valid, private=SECRET)]
        for field in valid:
            invalid.append({key: value for key, value in valid.items() if key != field})
        for field, values in [('version', [True, 1.0, 2, None]), ('runtime', ['other-page', None, []]),
                              ('operation', ['set', None, []]), ('mode', ['auto', 'FIT', None, [], True]),
                              ('saved_mode', ['auto', 'FIT', False, [], {}]), ('persisted', [1, 0, None, 'true', False])]:
            invalid.extend(dict(valid, **{field: value}) for value in values)
        invalid.extend([dict(current('fit', None), persisted=True), dict(current('fit', 'fill'), persisted=True)])
        for result in invalid:
            for machine in [False, True]:
                code, out, err, calls = self.run_cli(['aspect'], [caps(), result], machine)
                self.assertEqual((code, out, len(calls)), (1, '', 2))
                self.assertIn('invalid aspect metadata', err)
                self.assertNotIn(SECRET, err)

    def test_invalid_set_ack_never_claims_success_or_replays(self):
        valid = accepted()
        invalid = [None, [], {}, dict(valid, private=SECRET), dict(valid, persisted=True), dict(valid, saved_mode='fit')]
        for field in valid:
            invalid.append({key: value for key, value in valid.items() if key != field})
        for field, values in [('version', [True, 1.0, 2, None]), ('runtime', ['other-page', None, []]),
                              ('operation', ['get', None, []]), ('mode', ['fill', 'FIT', 'auto', None, [], True]),
                              ('accepted', [1, False, None, 'true']), ('dispatched', [0, True, None, 'false']),
                              ('effect', ['input-after-ack', None, True, []])]:
            invalid.extend(dict(valid, **{field: value}) for value in values)
        for result in invalid:
            for machine in [False, True]:
                code, out, err, calls = self.run_cli(['aspect', 'fit'], [caps(), result], machine)
                self.assertEqual((code, out, len(calls)), (1, '', 2))
                self.assertIn('do not repeat', err)
                self.assertNotIn(SECRET, err)

    def test_rejections_unsupported_clients_and_old_controllers_never_fallback(self):
        for failure in [ott.PlayerRejected(SECRET, {'error': SECRET}), ott.PlayerUnsupported(SECRET),
                        ott.HTTPError(400), ott.HTTPError(404)]:
            for before_request in [False, True]:
                responses = [failure] if before_request else [caps(), failure]
                for tail in [[], ['fit']]:
                    code, out, err, calls = self.run_cli(['aspect'] + tail, responses)
                    self.assertEqual((code, out, len(calls)), (1, '', 1 if before_request else 2))
                    self.assertNotIn(SECRET, err)
                    self.assertIn('No aspect command was sent' if before_request else 'The request was not repeated', err)
                    self.assertEqual([call.args[1] for call in calls], ['capabilities'] if before_request else ['capabilities', 'aspect'])

    def test_transport_uncertainty_never_replays_a_write(self):
        failure = ott.Error('The request may have been accepted; do not repeat the change blindly.')
        code, out, err, calls = self.run_cli(['aspect', 'fill'], [caps(), failure])
        self.assertEqual((code, out, len(calls)), (1, '', 2))
        self.assertIn('do not repeat', err)
        self.assertEqual(calls[-1], mock.call('dev_tv', 'aspect', {'operation': 'set', 'mode': 'fill', 'runtime': RUNTIME}))

    def test_one_total_deadline_stops_after_capabilities_and_restores_timeout(self):
        client = mock.Mock(timeout=45)
        client.call.return_value = caps()
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 45]), \
                self.assertRaisesRegex(ott.Error, 'no aspect command was sent'):
            ott.aspect_command(client, 'dev_tv', {'operation': 'set', 'mode': 'fit'}, 45)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {})])
        self.assertEqual(client.timeout, 45)

    def test_remaining_deadline_is_shared_and_late_ack_is_uncertain(self):
        for operation in ['get', 'set']:
            client = mock.Mock(timeout=45)
            params = {'operation': operation}
            if operation == 'set': params['mode'] = 'fit'
            reply = current() if operation == 'get' else accepted()
            budgets = []
            def respond(_device, action, _params):
                budgets.append(client.timeout)
                return caps() if action == 'capabilities' else reply
            client.call.side_effect = respond
            with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 3, 5]):
                self.assertEqual(ott.aspect_command(client, 'dev_tv', params, 45), reply)
            self.assertEqual(budgets, [45, 42])
            self.assertEqual(client.timeout, 45)
            client.call.reset_mock()
            with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 1, 45]), \
                    self.assertRaisesRegex(ott.Error, 'Acceptance is uncertain' if operation == 'set' else 'read aspect again'):
                ott.aspect_command(client, 'dev_tv', params, 45)
            self.assertEqual(client.call.call_count, 2)
            self.assertEqual(client.timeout, 45)

    def test_receipt_action_is_named_without_exposing_mode_or_payload(self):
        client = object.__new__(ott.Client)
        client.timeout = 45
        request_id = '1' * 32
        client.api = mock.Mock(return_value=(200, {'id': request_id}))
        client._wait_receipt = mock.Mock(return_value={'data': accepted()})
        receipts = []
        client.on_receipt = receipts.append
        self.assertEqual(client.call('dev_tv', 'aspect', {'operation': 'set', 'mode': 'fit', 'runtime': RUNTIME}), accepted())
        self.assertEqual(receipts, [{'action': 'aspect', 'request_id': request_id, 'status': 'ok'}])
        self.assertEqual(client.api.call_count, 1)


if __name__ == '__main__':
    unittest.main()
