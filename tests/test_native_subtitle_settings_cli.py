"""Native subtitle preferences use advertised types and opaque write receipts."""
import contextlib
import copy
import io
import json
import unittest
from unittest import mock

import test_player_settings_cli as generic


ott = generic.ott
RUNTIME = 'native-7'
CONTEXT = {'token': 'native-context:4e7c', 'provider': 'm3u', 'profile': 3}
REVISION = 'nsub:9a74e77c06a86a3d'
MODE = 'nativeSubtitleMode'
LANGUAGE = 'nativeSubtitleLanguage'
PRIVATE = 'private-source-detail-never-printed'
DESCRIPTION = ('Native HLS subtitle selection. Automatic selections update after saving. '
               'Manual track or Off choices take precedence until the next logical playback. '
               'Empty language follows manifest defaults. Plex retains its own preferences.')


def capabilities():
    caps = generic.caps()
    caps['player'].update(version='0.4.2', platform='webos-native', runtime=RUNTIME)
    return caps


def descriptor(identifier):
    row = {'id': identifier, 'type': 'string', 'scope': 'application', 'sensitive': False,
           'access': {'read': True, 'write': True}, 'apply': 'immediate',
           'default_value': 'off' if identifier == MODE else '', 'description': DESCRIPTION}
    if identifier == MODE:
        row['constraints'] = {'values': ['off', 'auto', 'forced']}
        row['choices'] = [{'value': value, 'label': label, 'aliases': []}
                          for value, label in (('off', 'Off (manual selection)'),
                                               ('auto', 'Automatic'), ('forced', 'Forced only'))]
    else:
        row['constraints'] = {'max_utf8_bytes': 35}
    return row


def schema(identifier, cursor=None):
    return {'version': 1, 'op': 'schema', 'runtime': RUNTIME, 'context': copy.deepcopy(CONTEXT),
            'settings': [descriptor(identifier)], 'next_cursor': cursor}


def response(operation, identifier, value=None, revision=None):
    if value is None:
        value = 'off' if identifier == MODE else ''
    if revision is None:
        revision = REVISION if operation == 'get' else 'nsub:9a74e77c06a86a3e'
    data = {'version': 1, 'op': operation, 'runtime': RUNTIME, 'context': copy.deepcopy(CONTEXT),
            'setting': {'id': identifier, 'revision': revision, 'storage': 'stored',
                        'value': value, 'persisted_value': value, 'effective_value': value}}
    if operation == 'get':
        data['descriptor'] = descriptor(identifier)
    else:
        data.update(changed=True, persisted=True, application={'state': 'completed'})
    return data


def rejected(reason, operation='set'):
    return ott.PlayerRejected(PRIVATE, {'version': 1, 'op': operation, 'runtime': RUNTIME,
                                        'error': PRIVATE, 'reason': reason})


class NativeSubtitleSettingsCLITest(unittest.TestCase):
    def run_cli(self, words, responses, *, machine=False):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'l_': 'native-device'}}
        client.credentials = {'devices': [{'id': 'native-device'}]}
        client.timeout = 45
        client.call = mock.Mock(side_effect=responses)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value=client.config), \
                mock.patch.object(ott, 'Client', return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['--json'] if machine else []) + ['l_', *words])
        self.assertEqual(client.timeout, 45)
        self.assertNotIn(PRIVATE, out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue(), client

    def assert_set(self, client, identifier, value, revision=REVISION):
        self.assertEqual(client.call.call_args_list, [
            mock.call('native-device', 'capabilities', {}),
            mock.call('native-device', 'player_settings', {'op': 'get', 'runtime': RUNTIME, 'id': identifier}),
            mock.call('native-device', 'player_settings', {'op': 'set', 'runtime': RUNTIME, 'id': identifier,
                      'context': CONTEXT['token'], 'revision': revision, 'value': value})])

    def test_native_schema_lists_canonical_modes_without_aliases_and_keeps_json(self):
        pages = [schema(MODE, 'next-page'), schema(LANGUAGE)]
        for machine in (False, True):
            with self.subTest(machine=machine):
                code, out, err, client = self.run_cli(['settings'], [capabilities(), *copy.deepcopy(pages)], machine=machine)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(client.call.call_count, 3)
                if machine:
                    expected = copy.deepcopy(pages[-1])
                    expected['settings'] = [descriptor(MODE), descriptor(LANGUAGE)]
                    self.assertEqual(json.loads(out), expected)
                else:
                    self.assertIn(MODE + ': string; "off", "auto", "forced"; application; writable', out)
                    self.assertIn(LANGUAGE + ': string; application; writable', out)
                self.assertEqual(client.call.call_args.args[2],
                                 {'op': 'schema', 'runtime': RUNTIME, 'limit': 20, 'cursor': 'next-page'})

    def test_each_native_mode_uses_the_exact_fresh_context_and_opaque_revision(self):
        for value in ('off', 'auto', 'forced'):
            with self.subTest(value=value):
                write = response('set', MODE, value, revision='nsub:opaque-new-receipt')
                code, out, err, client = self.run_cli(['setting', MODE, value],
                                                    [capabilities(), response('get', MODE), write])
                self.assertEqual((code, err), (0, ''))
                self.assertIn('saved ' + value, out)
                self.assertIn('Application: applied.', out)
                self.assertIn('Manual track or Off choices take precedence', out)
                self.assert_set(client, MODE, value)

    def test_native_reads_preserve_unset_defaults_without_any_write(self):
        for identifier in (MODE, LANGUAGE):
            with self.subTest(identifier=identifier):
                get = response('get', identifier)
                get['setting']['storage'] = 'default'
                get['setting'].pop('persisted_value')
                get['setting'].pop('effective_value')
                code, out, err, client = self.run_cli(['setting', identifier],
                                                    [capabilities(), get], machine=True)
                self.assertEqual((code, err, json.loads(out)), (0, '', get))
                self.assertEqual(client.call.call_args_list, [
                    mock.call('native-device', 'capabilities', {}),
                    mock.call('native-device', 'player_settings', {'op': 'get', 'runtime': RUNTIME, 'id': identifier})])

    def test_custom_and_empty_languages_are_preserved_without_cli_normalization(self):
        values = ('', 'en', 'eng', 'pt-BR', 'zh-Hant', 'es-419',
                  'en-abcdefgh-abcdefgh-abcdefgh-abcde')
        self.assertEqual(len(values[-1].encode('ascii')), 35)
        for value in values:
            with self.subTest(value=value):
                write = response('set', LANGUAGE, value)
                code, out, err, client = self.run_cli(['setting', LANGUAGE, value],
                                                    [capabilities(), response('get', LANGUAGE), write], machine=True)
                self.assertEqual((code, err, json.loads(out)), (0, '', write))
                self.assert_set(client, LANGUAGE, value)

    def test_bad_descriptors_stop_before_any_setting_write(self):
        cases = [('type', 'boolean'), ('id', 'anotherSetting'), ('access', []),
                 ('constraints', {'values': ['off', True]}),
                 ('constraints', {'pattern': 'off|auto|forced'}),
                 ('apply', 'view_loading'), ('scope', 'native'),
                 ('choices', [{'value': 'off', 'label': 'Off', 'aliases': ['bad alias']}])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                get = response('get', MODE)
                get['descriptor'][key] = value
                code, out, err, client = self.run_cli(['setting', MODE, 'auto'], [capabilities(), get])
                self.assertEqual((code, out, client.call.call_count), (1, '', 2))
                self.assertIn('invalid settings metadata', err)

    def test_modes_and_advertised_language_bounds_reject_locally_without_set(self):
        cases = [(MODE, value) for value in ('', 'AUTO', 'true', 'on', '1', 'forced-only')]
        cases += [(LANGUAGE, value) for value in ('a' * 36, 'é' * 18, 'en\nUS', 'en\x00US', 'en\x7f')]
        for identifier, value in cases:
            with self.subTest(identifier=identifier, value=repr(value)):
                code, out, err, client = self.run_cli(['setting', identifier, value],
                                                    [capabilities(), response('get', identifier)])
                self.assertEqual((code, out, client.call.call_count), (1, '', 2))
                self.assertIn('no change was sent', err)

    def test_invalid_read_receipts_cannot_be_used_for_conditional_write(self):
        for field, value in (('revision', None), ('revision', 'bad\nrevision'), ('context', ''),
                             ('runtime', 'another-runtime')):
            with self.subTest(field=field, value=repr(value)):
                get = response('get', MODE)
                if field == 'revision':
                    get['setting'][field] = value
                elif field == 'context':
                    get[field]['token'] = value
                else:
                    get[field] = value
                code, out, err, client = self.run_cli(['setting', MODE, 'auto'], [capabilities(), get])
                self.assertEqual((code, out, client.call.call_count), (1, '', 2))
                self.assertIn('invalid settings metadata', err)

    def test_language_semantics_remain_authoritative_on_the_player_without_replay(self):
        for value in ('en_US', 'r', 'en--US', 'abcd', 'ru-', 'Русский'):
            with self.subTest(value=value):
                code, out, err, client = self.run_cli(['setting', LANGUAGE, value],
                    [capabilities(), response('get', LANGUAGE), rejected('invalid_value')])
                self.assertEqual((code, out), (1, ''))
                self.assertIn('The value is invalid for this setting', err)
                self.assertIn('request was not repeated', err)
                self.assert_set(client, LANGUAGE, value)

    def test_stale_revision_runtime_and_source_rejections_never_retry_or_report_saved(self):
        for identifier, value in ((MODE, 'auto'), (LANGUAGE, 'es-419')):
            for reason in ('revision_conflict', 'runtime_changed', 'source_changed', 'policy_restricted', 'storage_failed'):
                with self.subTest(identifier=identifier, reason=reason):
                    code, out, err, client = self.run_cli(['setting', identifier, value],
                        [capabilities(), response('get', identifier), rejected(reason)])
                    self.assertEqual((code, out), (1, ''))
                    self.assertIn('request was not repeated', err)
                    self.assert_set(client, identifier, value)

    def test_unknown_write_outcome_never_repeats_get_set_or_reports_saved(self):
        for stage in ('enqueue', 'receipt'):
            with self.subTest(stage=stage):
                error = ott.RequestError('controller_unavailable' if stage == 'enqueue' else 'request_pending_timeout',
                                         stage, 'The controller did not confirm the request.',
                                         None if stage == 'enqueue' else 'a' * 32)
                code, out, err, client = self.run_cli(['setting', MODE, 'auto'],
                    [capabilities(), response('get', MODE), error])
                self.assertEqual((code, out), (1, ''))
                self.assertIn('did not confirm', err)
                self.assert_set(client, MODE, 'auto')

    def test_crossed_response_cannot_confirm_a_native_setting(self):
        for change in ('runtime', 'context', 'id', 'persisted_value'):
            with self.subTest(change=change):
                write = response('set', MODE, 'auto')
                if change == 'runtime':
                    write[change] = 'another-runtime'
                elif change == 'context':
                    write[change]['token'] = 'another-context'
                else:
                    write['setting'][change] = 'forced' if change == 'persisted_value' else LANGUAGE
                code, out, _, client = self.run_cli(['setting', MODE, 'auto'],
                                                   [capabilities(), response('get', MODE), write])
                self.assertEqual((code, out), (1, ''))
                self.assert_set(client, MODE, 'auto')

    def test_schema_source_change_and_missing_capability_do_not_guess_support(self):
        page = schema(LANGUAGE)
        page['context']['token'] = 'another-context'
        code, out, err, client = self.run_cli(['settings'], [capabilities(), schema(MODE, 'next-page'), page])
        self.assertEqual((code, out, client.call.call_count), (1, '', 3))
        self.assertIn('source changed', err)
        old = capabilities()
        old.pop('player_settings')
        code, out, _, client = self.run_cli(['setting', MODE, 'auto'], [old])
        self.assertEqual((code, out, client.call.call_count), (1, '', 1))

    def test_generic_schema_fallback_preserves_aliases_and_avoids_contradictory_values(self):
        row = descriptor(MODE)
        row['constraints']['values'] = ['off', 'auto', 'forced', 'off']
        row['choices'] = [{'value': 'off', 'label': 'Off', 'aliases': []},
                          {'value': 'auto', 'label': 'Automatic', 'aliases': ['automatic']},
                          {'value': 'not-permitted', 'label': 'Invalid', 'aliases': ['invalid']}]
        data = schema(MODE)
        data['settings'] = [row]
        before = copy.deepcopy(data)
        code, out, err, _ = self.run_cli(['settings'], [capabilities(), data])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('string; "off", automatic, "forced"; application', out)
        self.assertNotIn('not-permitted', out)
        self.assertNotIn('invalid', out)
        self.assertEqual(data, before)
        for value, kind in ((0, 'integer'), ('', 'string'), ('a,b"c', 'string')):
            data = schema(MODE)
            row = data['settings'][0]
            row.update(type=kind, default_value=value, constraints={'values': [value]})
            row.pop('choices')
            code, out, err, _ = self.run_cli(['settings'], [capabilities(), data])
            self.assertEqual((code, err), (0, ''))
            self.assertIn('; ' + json.dumps(value, ensure_ascii=False) + '; application', out)

    def test_sensitive_schema_values_remain_redacted_when_aliases_are_missing(self):
        for aliases in ([], ['configured']):
            with self.subTest(aliases=aliases):
                data = schema(MODE)
                row = data['settings'][0]
                row.update(id='privateSetting', sensitive=True, constraints={'values': [PRIVATE]},
                           choices=[{'value': PRIVATE, 'label': 'Configured', 'aliases': aliases}])
                row.pop('default_value')
                code, out, err, _ = self.run_cli(['settings'], [capabilities(), data])
                self.assertEqual((code, err), (0, ''))
                self.assertIn('values redacted', out)
                if aliases:
                    self.assertIn('; configured;', out)


if __name__ == '__main__':
    unittest.main()
