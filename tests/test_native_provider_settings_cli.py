"""Native credentials require relay negotiation and one exact conditional save."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_native_provider_tests', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
native = ott.native_provider_settings_module()
FIXTURE = json.loads((ROOT / 'contracts/native-provider-settings-v1.json').read_text())
PRIVATE = 'do-not-print-private-credentials'


class NativeProviderSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ott-native-provider-test-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'provider.json'
        self.doc = copy.deepcopy(FIXTURE['document'])
        self.caps = {
            'version': 1, 'player': {'version': 'fixture', 'platform': 'webos-native', 'runtime': 'native-runtime-1'},
            'input': [], 'lifecycle': [], 'playback': [], **copy.deepcopy(FIXTURE['capability']),
        }
        self.calls, self.overrides, self.timeouts = [], {}, []
        self.client = ott.Client.__new__(ott.Client)
        self.client.config = {'players': {'l': 'dev_1', 'lg2': 'dev_2'}}
        self.client.credentials = {'devices': [{'id': 'dev_1'}, {'id': 'dev_2'}]}
        self.client.server, self.client.timeout, self.client.on_receipt = 'https://controller.invalid', 45, None
        self.client.group_cancelled = None
        self.client.call = mock.Mock(side_effect=self.reply)

    def reply(self, device, action, params):
        self.calls.append((device, action, copy.deepcopy(params)))
        self.timeouts.append(self.client.timeout)
        if action in self.overrides:
            value = self.overrides[action]
            if isinstance(value, BaseException):
                raise value
            return value(device, params) if callable(value) else copy.deepcopy(value)
        if action == 'capabilities':
            return copy.deepcopy(self.caps)
        if action == 'settings_compare':
            return {**{key: params[key] for key in ('version', 'runtime', 'provider', 'number')},
                    'context': 'context-123', 'revision': 'revision-456', 'matched': False}
        if action == 'provider_settings':
            return {**{key: params[key] for key in ('version', 'runtime', 'provider', 'number', 'context', 'revision')},
                    'saved': True, 'fields': list(params['settings']), 'reloaded': True, 'reload_required': False}
        raise AssertionError((device, action, params))

    def run_cli(self, tail=None, *, document=None, target='l', json_output=True, raw=None):
        if raw is not None:
            self.path.write_text(raw)
        else:
            value = self.doc if document is None else document
            serialized = json.dumps(value, ensure_ascii=False)
            try:
                serialized.encode('utf-8')
            except UnicodeError:
                serialized = json.dumps(value, ensure_ascii=True)
            self.path.write_text(serialized)
        words = ['provider-config', *(tail if tail is not None else [str(self.path)])]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=self.client), \
                mock.patch.object(ott, 'read_json', return_value=self.client.config), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['-j'] if json_output else []) + [target, *words])
        self.assertEqual(self.client.timeout, 45)
        self.assertNotIn(PRIVATE, out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def actions(self):
        return [row[1] for row in self.calls]

    def test_shared_fixture_compare_and_save_once(self):
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), FIXTURE['set_result'])
        self.assertNotIn('fixture_key', out)
        self.assertEqual(self.calls, [('dev_1', FIXTURE[key]['action'], FIXTURE[key]['params'])
                                     for key in ('probe', 'compare_request', 'set_request')])
        self.assertEqual(set(native.PROVIDERS), set(FIXTURE['provider_ids']))
        self.assertEqual(set(native.REASONS), set(FIXTURE['reasons']))

    def test_matching_comparison_skips_save_and_reload(self):
        self.overrides['settings_compare'] = dict(FIXTURE['compare_result'], matched=True)
        code, out, err = self.run_cli(json_output=False)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('settings match; no save was sent', out)
        self.assertEqual(self.actions(), ['capabilities', 'settings_compare'])

    def test_check_is_read_only_for_both_comparison_outcomes(self):
        for matched in (False, True):
            with self.subTest(matched=matched):
                self.overrides['settings_compare'] = dict(FIXTURE['compare_result'], matched=matched)
                self.calls.clear()
                code, out, err = self.run_cli(['--check', str(self.path)])
                self.assertEqual((code, err), (0, ''))
                self.assertIs(json.loads(out)['matched'], matched)
                self.assertEqual(self.actions(), ['capabilities', 'settings_compare'])

    def test_schema_negotiates_without_transmitting_private_file(self):
        for arguments in (['--schema'], ['--schema', 'edem'], ['--schema', 'd/maxtv']):
            with self.subTest(arguments=arguments):
                self.calls.clear()
                code, out, err = self.run_cli(arguments)
                self.assertEqual((code, err), (0, ''))
                data = json.loads(out)
                self.assertEqual(data['operation'], 'schema')
                self.assertIn('edem' if len(arguments) == 1 else arguments[1], data['providers'])
                self.assertEqual(self.calls, [('dev_1', 'capabilities', {'native_provider_settings': True})])
                self.assertNotIn('fixture_key', out)
        code, out, err = self.run_cli(['--schema', 'edem'], json_output=False)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Required pattern: [A-Za-z0-9_-]{1,512}', out)
        self.assertIn('player-validated VPortal link', out)

    def test_capability_accepts_reverse_operations_and_subset(self):
        self.caps['native_provider_settings']['operations'] = ['set', 'compare']
        self.caps['native_provider_settings']['providers'] = ['edem']
        code, _, err = self.run_cli()
        self.assertEqual((code, err), (0, ''))
        checked = ott.capabilities_metadata(self.caps)['native_provider_settings']
        self.assertEqual(checked['operations'], ['compare', 'set'])

    def test_future_compatible_platform_and_full_runtime_token(self):
        self.caps['player']['platform'] = 'future-player'
        self.caps['player']['runtime'] = 'Native:' + 'a' * 121
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['runtime'], self.caps['player']['runtime'])
        self.assertEqual(len(self.calls[1][2]['runtime']), 128)
        legacy = copy.deepcopy(self.caps)
        del legacy['native_provider_settings']
        with self.assertRaises(ott.Error):
            ott.capabilities_metadata(legacy)
        legacy['native_provider_settings'] = {'version': 1, 'operations': ['compare', 'compare'], 'providers': ['edem']}
        with self.assertRaises(ott.Error):
            ott.capabilities_metadata(legacy)

    def test_missing_or_invalid_marker_stops_before_credentials(self):
        good = copy.deepcopy(self.caps['native_provider_settings'])
        bad = [None, {}, True, dict(good, version=True), dict(good, version=1.0),
               dict(good, version=2), dict(good, operations=['compare', 'compare']),
               dict(good, operations=['compare', 'set', 'get']), dict(good, operations=[[], 'set']),
               dict(good, providers=[]), dict(good, providers=['edem', 'edem']),
               dict(good, providers=['ilook']), dict(good, providers=['operator']),
               dict(good, providers=[True]), dict(good, private=PRIVATE)]
        for marker in bad:
            with self.subTest(marker=marker):
                self.caps['native_provider_settings'] = marker
                self.calls.clear()
                code, _, err = self.run_cli()
                self.assertEqual(code, 1)
                self.assertIn('no credentials or save were sent', err)
                self.assertEqual(self.actions(), ['capabilities'])
        del self.caps['native_provider_settings']
        self.calls.clear()
        code, _, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(self.actions(), ['capabilities'])

    def test_unadvertised_provider_never_sends_credentials(self):
        self.caps['native_provider_settings']['providers'] = ['ottclub']
        code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn('not advertised', err)
        self.assertEqual(self.actions(), ['capabilities'])

    def test_old_controller_and_probe_failures_never_fall_back(self):
        errors = [ott.HTTPError(code) for code in (400, 404, 405, 413, 500, 503)]
        errors += [ott.PlayerRejected(PRIVATE, {'error': PRIVATE}), ott.PlayerUnsupported(PRIVATE),
                   ott.TransportError(PRIVATE), ott.RequestError('controller_unavailable', 'enqueue', PRIVATE)]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.calls.clear()
                self.overrides['capabilities'] = error
                code, _, _ = self.run_cli()
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, [('dev_1', 'capabilities', {'native_provider_settings': True})])

    def test_canonical_registry_and_closed_patch_types(self):
        for provider in FIXTURE['provider_ids']:
            with self.subTest(provider=provider):
                doc = {'provider': provider, 'number': 15, 'settings': {'list': 3}}
                self.assertEqual(native.document(doc)['settings'], {'list': 3})
        for doc in [dict(self.doc, provider=value) for value in ('ilook', 'ilooktv', 'operator', 'plex', '../edem', ['edem'])] + [
                dict(self.doc, number=value) for value in (0, 16, True, 3.0, '3', None)] + [
                dict(self.doc, settings=value) for value in ({}, None, [], {'key': ''}, {'key': 'x' * 513},
                    {'key': 'secret/key'}, {'key': 'ключ'}, {'key': PRIVATE, 'cdn': 'host'},
                    {'list': True}, {'list': 1.0}, {'list': 4}, {'list': -1}, {'list': '2'},
                    {'portal': '\ud800'}, {'portal': 'x\ny'}, {'portal': 'x\u0085y'},
                    {'portal': 'é' * 4097}, {'portal': []})]:
            with self.subTest(doc=doc):
                self.calls.clear()
                code, _, _ = self.run_cli(document=doc)
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, [])
        for patch in ({'mode': True}, {'mode': 3}, {'mode': -1}, {'mode': 2.0}, {'list': 4},
                      {'username': []}, {'password': None}, {'server': '\udfff'}, {'mac': 'x\x7fy'},
                      {'playlist': 'é' * 4097}, {'key': PRIVATE}, {'login': PRIVATE}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                native.document({'provider': 'ottclub', 'number': 1, 'settings': patch})

    def test_patch_boundaries_preserve_empty_and_unspecified_fields(self):
        for provider, patch in [('edem', {'key': 'x' * 512, 'list': 0, 'portal': ''}),
                                ('d/maxtv', {'username': 'é' * 4096}),
                                ('ottclub', {'server': '', 'password': '', 'playlist': '', 'mac': '', 'list': 0, 'mode': 2})]:
            with self.subTest(provider=provider):
                self.calls.clear()
                doc = {'provider': provider, 'number': 1, 'settings': patch}
                code, out, err = self.run_cli(document=doc)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(self.calls[1][2]['settings'], patch)
                self.assertEqual(self.calls[2][2]['settings'], patch)
                self.assertEqual(set(json.loads(out)['fields']), set(patch))
                if 'username' in patch:
                    self.assertNotIn(patch['username'], out)

    def test_duplicate_nonfinite_unknown_fields_and_oversized_files_stop_locally(self):
        raws = [
            '{"provider":"edem","number":1,"settings":{"list":1,"list":2}}',
            '{"provider":"edem","number":1,"number":2,"settings":{"list":1}}',
            '{"provider":"edem","number":1,"settings":{"list":NaN}}',
            '{"provider":"edem","number":1,"settings":{"list":Infinity}}',
            json.dumps(dict(self.doc, secret=PRIVATE)),
            json.dumps(dict(self.doc, settings={'portal': 'x' * 17000})),
            '{"provider":"edem","number":1,"settings":{"portal":' + '[' * 1500 +
            '"' + PRIVATE + '"' + ']' * 1500 + '}}',
        ]
        for raw in raws:
            with self.subTest(raw_size=len(raw)):
                self.calls.clear()
                code, _, _ = self.run_cli(raw=raw)
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, [])

    def test_legacy_two_key_format_keeps_original_wire(self):
        for provider in ('m3u', 'xtream', 'stalker', 'ottclub'):
            with self.subTest(provider=provider):
                self.calls.clear()
                self.overrides['provider_settings'] = {'provider': provider, 'saved': True, 'fields': ['legacy_driver_field']}
                doc = {'provider': provider, 'settings': {'legacy_driver_field': PRIVATE}}
                code, _, err = self.run_cli(document=doc)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(self.calls, [('dev_1', 'provider_settings', doc)])
        self.calls.clear()
        code, _, _ = self.run_cli(['--check', str(self.path)], document={'provider': 'm3u', 'settings': {'playlist': 'x'}})
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])

    def test_invalid_command_shapes_never_contact_player(self):
        for tail in ([], ['--check'], ['--schema', 'edem', 'extra'], ['--schema', 'ilook'],
                     ['--schema', 'unknown'], ['--check', str(self.path), 'extra']):
            with self.subTest(tail=tail):
                self.calls.clear()
                code, _, _ = self.run_cli(tail)
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, [])

    def test_compare_identity_shape_and_tokens_checked_before_save(self):
        for changes in ({'version': True}, {'version': 1.0}, {'runtime': 'other'}, {'provider': 'ottclub'},
                        {'number': 2}, {'number': True}, {'matched': 0}, {'context': ''},
                        {'context': 'x' * 129}, {'revision': 'https://private.invalid'}, {'settings': {'key': PRIVATE}}):
            with self.subTest(changes=changes):
                self.calls.clear()
                self.overrides['settings_compare'] = dict(FIXTURE['compare_result'], **changes)
                code, _, err = self.run_cli()
                self.assertEqual(code, 1)
                self.assertIn('no save was sent', err)
                self.assertEqual(self.actions(), ['capabilities', 'settings_compare'])

    def test_response_data_size_is_bounded_before_projection(self):
        data = dict(FIXTURE['compare_result'], reason=PRIVATE * 100)
        self.assertGreater(native.encoded_size(data), native.MAX_RESULT_BYTES)
        with self.assertRaises(ValueError):
            native.metadata(data, FIXTURE['compare_request']['params'])
        self.overrides['settings_compare'] = data
        code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn('no save was sent', err)
        self.assertNotIn('provider_settings', self.actions())

    def test_save_identity_tokens_field_set_and_flags_checked_without_replay(self):
        for changes in ({'version': True}, {'runtime': 'other'}, {'provider': 'ottclub'}, {'number': 4},
                        {'number': True}, {'context': 'other'}, {'revision': 'new-write-authority'},
                        {'saved': 1}, {'saved': False}, {'fields': ['key', 'key']}, {'fields': ['key']},
                        {'fields': ['key', 'list', 'extra']}, {'fields': ['key', True]},
                        {'reloaded': 1}, {'reload_required': True}, {'settings': {'key': PRIVATE}}):
            with self.subTest(changes=changes):
                self.calls.clear()
                self.overrides['provider_settings'] = dict(FIXTURE['set_result'], **changes)
                code, _, err = self.run_cli()
                self.assertEqual(code, 1)
                self.assertIn('outcome is unknown', err)
                self.assertIn('not repeated', err)
                self.assertEqual(self.actions(), ['capabilities', 'settings_compare', 'provider_settings'])

    def test_field_order_is_irrelevant_and_reload_flags_are_honest(self):
        self.overrides['provider_settings'] = dict(FIXTURE['set_result'], fields=['list', 'key'],
                                                   reloaded=False, reload_required=True)
        code, out, err = self.run_cli(json_output=False)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('settings saved', out)
        self.assertIn('reloading the source is still required', out)
        self.assertNotIn('fixture_key', out)

    def test_all_closed_rejections_are_safe_and_never_retry(self):
        for action in ('settings_compare', 'provider_settings'):
            for reason in FIXTURE['reasons']:
                with self.subTest(action=action, reason=reason):
                    self.overrides.clear()
                    self.calls.clear()
                    data = dict(FIXTURE['rejected_result'], reason=reason)
                    error_type = ott.PlayerUnsupported if reason == 'unsupported' else ott.PlayerRejected
                    self.overrides[action] = error_type(PRIVATE, data)
                    code, _, err = self.run_cli()
                    self.assertEqual(code, 1)
                    self.assertIn('No save was sent' if action == 'settings_compare' else 'not repeated', err)
                    self.assertEqual(self.actions().count(action), 1)
                    if action == 'settings_compare':
                        self.assertNotIn('provider_settings', self.actions())

    def test_malformed_rejection_is_not_confirmed_and_cannot_echo_secrets(self):
        for action in ('settings_compare', 'provider_settings'):
            for changes in ({'reason': PRIVATE}, {'error': PRIVATE}, {'runtime': 'other'},
                            {'provider': 'ottclub'}, {'number': True}, {'version': 2}):
                with self.subTest(action=action, changes=changes):
                    self.overrides.clear()
                    self.calls.clear()
                    self.overrides[action] = ott.PlayerRejected(PRIVATE, dict(FIXTURE['rejected_result'], **changes))
                    code, _, err = self.run_cli()
                    self.assertEqual(code, 1)
                    self.assertIn('invalid provider configuration rejection', err)
                    self.assertIn('outcome is unknown' if action == 'provider_settings' else 'No save was sent', err)
                    self.assertEqual(self.actions().count(action), 1)

    def test_transport_protocol_and_unexpected_failures_are_secret_free(self):
        for action in ('settings_compare', 'provider_settings'):
            errors = [ott.TransportError(PRIVATE), ott.Error(PRIVATE), OSError(PRIVATE), RuntimeError(PRIVATE),
                      ott.RequestError('controller_unavailable', 'receipt', PRIVATE, 'a' * 32, transport_category='socket')]
            for error in errors:
                with self.subTest(action=action, error=type(error).__name__):
                    self.overrides.clear()
                    self.calls.clear()
                    self.overrides[action] = error
                    code, _, err = self.run_cli()
                    self.assertEqual(code, 1)
                    self.assertIn('outcome is unknown' if action == 'provider_settings' else 'no save was sent', err)
                    self.assertEqual(self.actions().count(action), 1)

    def test_http_failure_never_repeats_or_uses_unbound_route(self):
        for action in ('settings_compare', 'provider_settings'):
            for status in (400, 403, 404, 413, 429, 500, 503):
                with self.subTest(action=action, status=status):
                    self.overrides.clear()
                    self.calls.clear()
                    self.overrides[action] = ott.HTTPError(status)
                    code, _, err = self.run_cli()
                    self.assertEqual(code, 1)
                    self.assertEqual(self.actions().count(action), 1)
                    self.assertTrue(all(row[2].get('version') == 1 for row in self.calls[1:]))
                    if action == 'provider_settings':
                        self.assertIn('not repeated', err)

    def test_shared_deadline_does_not_refresh_between_steps(self):
        now = [0]
        original = self.client.call.side_effect
        def advance(device, action, params):
            result = original(device, action, params)
            now[0] += 10
            return result
        self.client.call.side_effect = advance
        with mock.patch.object(native.time, 'monotonic', side_effect=lambda: now[0]):
            code, _, err = self.run_cli()
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(self.timeouts, [45, 35, 25])

    def test_timeout_at_each_stage_stops_without_replay(self):
        for expired_action in ('capabilities', 'settings_compare', 'provider_settings'):
            with self.subTest(action=expired_action):
                self.calls.clear()
                now = [0]
                original = self.reply
                def expire(device, action, params):
                    result = original(device, action, params)
                    if action == expired_action:
                        now[0] = 46
                    return result
                self.client.call.side_effect = expire
                with mock.patch.object(native.time, 'monotonic', side_effect=lambda: now[0]):
                    code, _, err = self.run_cli()
                self.assertEqual(code, 1)
                self.assertIn('outcome is unknown' if expired_action == 'provider_settings' else 'no save was sent', err)
                self.assertEqual(self.actions().count(expired_action), 1)
                if expired_action != 'provider_settings':
                    self.assertNotIn('provider_settings', self.actions())

    def test_group_cancellation_between_comparison_and_save_prevents_write(self):
        cancelled = threading.Event()
        self.client.group_cancelled = cancelled
        self.overrides['settings_compare'] = lambda *_: (cancelled.set(), copy.deepcopy(FIXTURE['compare_result']))[1]
        code, out, err = self.run_cli()
        self.assertEqual(code, 130)
        self.assertEqual(self.actions(), ['capabilities', 'settings_compare'])
        self.assertEqual((out, err), ('', ''))

    def test_cancellation_during_write_does_not_send_recovery_or_retry(self):
        self.overrides['provider_settings'] = KeyboardInterrupt()
        code, _, _ = self.run_cli()
        self.assertEqual(code, 130)
        self.assertEqual(self.actions(), ['capabilities', 'settings_compare', 'provider_settings'])

    def test_group_binds_each_target_separately_and_saves_concurrently(self):
        barrier = threading.Barrier(2)
        self.overrides['settings_compare'] = lambda device, params: {
            **{key: params[key] for key in ('version', 'runtime', 'provider', 'number')},
            'context': 'ctx-' + device, 'revision': 'rev-' + device, 'matched': False}
        self.overrides['provider_settings'] = lambda device, params: (
            barrier.wait(timeout=5), {**{key: params[key] for key in
                                       ('version', 'runtime', 'provider', 'number', 'context', 'revision')},
                                     'fields': list(params['settings']), 'saved': True,
                                     'reloaded': False, 'reload_required': False})[1]
        code, out, err = self.run_cli(target='l,lg2')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['succeeded'], 2)
        for device in ('dev_1', 'dev_2'):
            calls = [row for row in self.calls if row[0] == device]
            self.assertEqual([row[1] for row in calls], ['capabilities', 'settings_compare', 'provider_settings'])
            self.assertEqual(calls[-1][2]['context'], 'ctx-' + device)
            self.assertEqual(calls[-1][2]['revision'], 'rev-' + device)

    def test_group_transport_metadata_retains_only_safe_request_identity(self):
        error = ott.RequestError('controller_unavailable', 'receipt', PRIVATE, 'a' * 32, transport_category='socket')
        error.action = 'provider_settings'
        self.overrides['provider_settings'] = error
        code, out, _ = self.run_cli(target='l,lg2')
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(data['failed'], 2)
        for row in data['results']:
            self.assertEqual(row['request']['action'], 'provider_settings')
            self.assertEqual(row['request']['request_id'], 'a' * 32)
            self.assertIn('outcome is unknown', row['error'])

    def test_queued_envelope_bound_includes_html_escaping_and_transport_fields(self):
        doc = {'provider': 'ottclub', 'number': 1, 'settings': {'password': '<' * 3000}}
        code, _, err = self.run_cli(document=doc)
        self.assertEqual(code, 1)
        self.assertIn('exceeds 16 KiB', err)
        self.assertEqual(self.actions(), ['capabilities'])
        request = dict(FIXTURE['compare_request']['params'], provider='ottclub', settings={'password': '&\u2028\u2029'})
        plain = len(json.dumps({'action': 'settings_compare', 'params': request}, ensure_ascii=False).encode())
        self.assertGreater(native.request_size('settings_compare', request), plain + 64)

    def test_set_envelope_is_checked_again_after_receipt_tokens(self):
        doc = {'provider': 'ottclub', 'number': 1, 'settings': {'password': 'x' * 8100, 'username': 'y' * 8100}}
        request = dict(FIXTURE['compare_request']['params'], provider='ottclub', number=1, settings=doc['settings'])
        # Find a boundary where compare fits but adding both maximal tokens does not.
        while native.request_size('settings_compare', request) > native.MAX_REQUEST_BYTES:
            doc['settings']['username'] = doc['settings']['username'][:-1]
        self.overrides['settings_compare'] = dict(FIXTURE['compare_result'], provider='ottclub', number=1,
                                                   context='c' * 128, revision='r' * 128)
        code, _, err = self.run_cli(document=doc)
        self.assertEqual(code, 1)
        self.assertIn('exceeds 16 KiB', err)
        self.assertEqual(self.actions(), ['capabilities', 'settings_compare'])

    def test_help_advertises_only_negotiated_native_configuration(self):
        for present in (False, True):
            controls = copy.deepcopy(self.caps)
            if not present:
                del controls['native_provider_settings']
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ott.print_player_help({'capabilities': controls}, 'l')
            self.assertEqual('provider-config --schema' in output.getvalue(), present)


if __name__ == '__main__':
    unittest.main()
