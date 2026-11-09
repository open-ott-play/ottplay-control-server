"""Full presets keep native credentials conditional, numbered and recoverable."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

import test_presets_cli as presets
from test_preset_session import ExactPlayer, session

ott = presets.ott
native = ott.native_provider_settings_module()
FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'contracts/native-provider-settings-v1.json').read_text())
PRIVATE = 'native-private-test-credential'


def configuration(rows=None):
    result = presets.config()
    result['presets']['local']['native_provider_settings'] = copy.deepcopy(rows if rows is not None else [
        {'provider': 'edem', 'number': 3, 'settings': {'key': PRIVATE, 'list': 2}},
        {'provider': 'd/maxtv', 'number': 7, 'settings': {'username': 'test-user', 'password': PRIVATE}},
    ])
    return result


class NativePlayer(ExactPlayer):
    def __init__(self):
        super().__init__()
        self.bound_calls, self.native_saves, self.tickets = [], [], {}
        self.caps = {'version': 1, 'player': {'version': 'fixture', 'platform': 'webos-native',
                    'runtime': 'runtime-native'}, 'input': [], 'lifecycle': [], 'playback': [],
                    **copy.deepcopy(FIXTURE['capability'])}
        self.native_state = {}
        self.serial = 0
        self.native_fault = None
        self.native_loss = None
        self.checkpoint = None
        self.intent_snapshots = []

    def call(self, device, action, params, idempotency_key=None):
        is_native = action == 'capabilities' and params == {'native_provider_settings': True} or params.get('version') == 1
        if not is_native:
            return super().call(device, action, params, idempotency_key)
        self.bound_calls.append((action, copy.deepcopy(params), idempotency_key))
        self.calls.append((device, action, copy.deepcopy(params)))
        if self.native_fault is not None:
            answer = self.native_fault(action, params)
            if answer is not None:
                return answer
        if action == 'capabilities':
            return copy.deepcopy(self.caps)
        identity = {key: params[key] for key in ('version', 'runtime', 'provider', 'number')}
        key = params['provider'], params['number']
        if action == 'settings_compare':
            self.serial += 1
            token = 'revision-' + str(self.serial)
            self.tickets[token] = copy.deepcopy(params)
            while len(self.tickets) > 64:
                del self.tickets[next(iter(self.tickets))]
            current = self.native_state.get(key, {})
            return {**identity, 'context': 'context-' + str(self.serial), 'revision': token,
                    'matched': all(current.get(field) == value for field, value in params['settings'].items())}
        if action == 'provider_settings':
            self.native_saves.append(copy.deepcopy(params))
            compared = self.tickets.pop(params['revision'])
            assert compared == {key: value for key, value in params.items() if key not in ('context', 'revision')}
            assert params['context'] == 'context-' + params['revision'].split('-')[-1]
            if self.checkpoint is not None:
                self.intent_snapshots.append(json.loads(self.checkpoint.read_text()))
            if self.native_loss != 'before':
                self.native_state.setdefault(key, {}).update(copy.deepcopy(params['settings']))
                if idempotency_key:
                    self.operations[idempotency_key] = {'state': 'responded', 'result_status': 'ok'}
            if self.native_loss is not None:
                self.native_loss = None
                raise ott.Error(PRIVATE + ' acknowledgement lost')
            return {**identity, 'context': params['context'], 'revision': params['revision'],
                    'saved': True, 'fields': list(params['settings']), 'reloaded': False, 'reload_required': False}
        raise AssertionError(action)


class NativeProviderPresetTests(unittest.TestCase):
    def setUp(self):
        self.source = configuration()
        self.name, self.preset = ott.validate_preset(self.source, 'local')
        self.player = NativePlayer()
        self.temp = tempfile.TemporaryDirectory(prefix='ott-native-preset-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'checkpoint.json'

    def load(self, session_value=None):
        result = ott.load_preset(self.player, 'dev_tv', self.name, self.preset, session=session_value)
        self.assertNotIn(PRIVATE, json.dumps(result))
        self.assertEqual(self.player.timeout, 45)
        return result

    def run_session(self, resume=False):
        current = session.Session(ott, self.player, 'dev_tv', self.name, self.preset, self.path, resume)
        self.player.checkpoint = self.path
        try:
            return self.load(current)
        finally:
            current.close()

    def test_document_validation_and_canonical_order(self):
        self.assertEqual([row['provider'] for row in self.preset['native_provider_settings']], ['d/maxtv', 'edem'])
        self.assertEqual(self.preset['native_provider_settings'][1], self.source['presets']['local']['native_provider_settings'][0])
        self.preset['native_provider_settings'][1]['settings']['key'] = 'different'
        self.assertEqual(self.source['presets']['local']['native_provider_settings'][0]['settings']['key'], PRIVATE)
        invalid = [None, {}, [], [self.source['presets']['local']['native_provider_settings'][0]] * 2]
        for change in ({'provider': 'ilook'}, {'provider': 'plex'}, {'provider': 'unknown'},
                       {'number': True}, {'number': 0}, {'number': 16}, {'number': 2.5},
                       {'settings': {}}, {'settings': {'list': True}}, {'settings': {'key': 'bad key'}},
                       {'settings': {'key': PRIVATE, 'unknown': PRIVATE}}, {'unknown': PRIVATE}):
            invalid.append([{**self.source['presets']['local']['native_provider_settings'][0], **change}])
        invalid.append([{'provider': 'ottclub', 'number': 1,
                         'settings': {'server': '<' * 8192, 'password': PRIVATE}}])
        for rows in invalid:
            with self.subTest(rows=type(rows).__name__):
                invalid_source = configuration()
                invalid_source['presets']['local']['native_provider_settings'] = rows
                with self.assertRaises(ott.Error) as error:
                    ott.validate_preset(invalid_source, 'local')
                self.assertNotIn(PRIVATE, str(error.exception))
        self.assertEqual(self.player.calls, [])

    def test_registry_accepts_every_explicit_slot_and_rejects_661_rows(self):
        rows = [{'provider': provider, 'number': number,
                 'settings': {'key': PRIVATE} if provider == 'edem' else {'password': PRIVATE}}
                for provider in FIXTURE['provider_ids'] for number in range(1, 16)]
        _, normalized = ott.validate_preset(configuration(rows), 'local')
        self.assertEqual(len(normalized['native_provider_settings']), 660)
        with self.assertRaises(ott.Error):
            ott.validate_preset(configuration(rows + [rows[0]]), 'local')

    def test_load_preflights_every_slot_then_fresh_compare_save_and_readback(self):
        result = self.load()
        self.assertEqual((result['status'], self.player.provider, self.player.active), ('loaded', 'm3u', 1))
        self.assertEqual([row['provider'] for row in self.player.native_saves], ['d/maxtv', 'edem'])
        first_write = next(i for i, (_, action, _) in enumerate(self.player.calls) if action == 'provider_settings')
        self.assertEqual([action for _, action, _ in self.player.calls[:first_write]],
                         ['capabilities', 'settings_compare', 'capabilities', 'settings_compare', 'capabilities', 'settings_compare'])
        for index, (action, params, key) in enumerate(self.player.bound_calls):
            self.assertIsNone(key)
            if action == 'provider_settings':
                previous = self.player.bound_calls[index - 1]
                self.assertEqual(previous[0], 'settings_compare')
                self.assertEqual(previous[1], {key: value for key, value in params.items() if key not in ('context', 'revision')})
                self.assertNotIn(params['revision'], ('revision-1', 'revision-2'))
        self.assertEqual([params['query'] for _, action, params in self.player.calls if action == 'provider'], ['plex', 'm3u'])
        self.assertEqual(result['completed'][-2:], ['verify_native_d/maxtv_7', 'verify_native_edem_3'])

    def test_matching_native_slots_are_not_saved_or_selected(self):
        for row in self.preset['native_provider_settings']:
            self.player.native_state[row['provider'], row['number']] = copy.deepcopy(row['settings'])
        self.assertEqual(self.load()['status'], 'loaded')
        self.assertEqual(self.player.native_saves, [])

    def test_more_than_64_preflight_tickets_still_use_fresh_bound_saves(self):
        rows = [{'provider': provider, 'number': number, 'settings': {'password': PRIVATE}}
                for provider in sorted(native.OPERATORS)[:5] for number in range(1, 16)]
        self.player.caps['native_provider_settings']['providers'] = sorted(native.PROVIDERS)
        _, self.preset = ott.validate_preset(configuration(rows), 'local')
        self.assertEqual(self.load()['status'], 'loaded')
        self.assertEqual(len(self.player.native_saves), 75)
        self.assertTrue(all(int(row['revision'].split('-')[-1]) > 75 for row in self.player.native_saves))

    def test_missing_marker_or_unadvertised_later_provider_fails_before_any_mutation(self):
        for marker in (None, {'version': 1, 'operations': ['compare', 'set'], 'providers': ['d/maxtv']}):
            self.player = NativePlayer()
            if marker is None:
                del self.player.caps['native_provider_settings']
            else:
                self.player.caps['native_provider_settings'] = marker
            result = self.load()
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['stage'].startswith('preflight_native_'))
            self.assertIn('sent no preset settings', result['error'])
            self.assertTrue(all(action in ('capabilities', 'settings_compare') for _, action, _ in self.player.calls))
            if marker is None:
                self.assertEqual(len(self.player.calls), 1)

    def test_native_preflight_happens_before_local_connections(self):
        self.preset['local'] = {'sources': [{'name': 'Movies'}]}
        self.player.caps.pop('native_provider_settings')
        with mock.patch.object(ott, 'local_preset_plan', return_value=[{'name': 'Movies', 'selector': 'folder-1', 'connected': False}]), \
                mock.patch.object(ott, 'local_media_command') as connect:
            self.assertEqual(self.load()['status'], 'failed')
        connect.assert_not_called()

    def test_malformed_comparison_and_lost_save_never_leak_or_repeat(self):
        self.player.native_fault = lambda action, params: {'private': PRIVATE} if action == 'settings_compare' else None
        self.assertEqual(self.load()['status'], 'failed')
        self.assertEqual(self.player.native_saves, [])
        self.player = NativePlayer()
        self.player.native_loss = 'after'
        result = self.load()
        self.assertEqual((result['status'], result['stage']), ('failed', 'save_native_d/maxtv_7'))
        self.assertEqual(len(self.player.native_saves), 1)
        self.assertEqual(self.player.calls[-1][1], 'provider_settings')

    def test_final_comparison_detects_native_drift_without_repair(self):
        def drift(action, params):
            if action == 'capabilities' and self.player.provider == 'm3u' and len(self.player.native_saves) == 2:
                self.player.native_state.clear()
        self.player.native_fault = drift
        result = self.load()
        self.assertEqual((result['status'], result['stage']), ('failed', 'verify_native_d/maxtv_7'))
        self.assertEqual(len(self.player.native_saves), 2)

    def test_plan_is_offline_and_verify_is_bound_read_only(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(session.run(ott, self.source, ['tv', 'load', 'local', 'plan'], 45, True, None,
                                         client=self.player, device='dev_tv'), 0)
        self.assertEqual(self.player.calls, [])
        self.assertEqual(json.loads(output.getvalue())['settings'][-2:], ['native_d/maxtv_7', 'native_edem_3'])
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertEqual(self.load()['status'], 'loaded')
        self.player.calls.clear()
        self.player.bound_calls.clear()
        result = session.verify(ott, self.player, 'dev_tv', self.preset)
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(all(action in ('capabilities', 'settings_compare', 'status', 'profiles') for _, action, _ in self.player.calls))
        self.assertTrue(all(key is None for _, _, key in self.player.bound_calls))

    def test_verify_reports_native_provider_without_selecting_m3u(self):
        self.assertEqual(self.load()['status'], 'loaded')
        for provider in ('d/maxtv', 'iptv-ott.ru'):
            self.player.provider = provider
            self.player.calls.clear()
            result = session.verify(ott, self.player, 'dev_tv', self.preset)
            self.assertEqual((result['status'], result['provider'], result['active_profile']), ('different', provider, None))
            self.assertNotIn('profiles', [action for _, action, _ in self.player.calls])
        self.player.provider = 'unknown/native'
        with self.assertRaises(ott.Error):
            session.verify(ott, self.player, 'dev_tv', self.preset)

    def test_session_journals_only_write_and_checkpoint_contains_no_credentials_or_tokens(self):
        self.assertEqual(self.run_session()['status'], 'loaded')
        for action, params, key in self.player.bound_calls:
            self.assertEqual(key is not None, action == 'provider_settings')
        self.assertEqual(len(self.player.intent_snapshots), 2)
        for snapshot, request in zip(self.player.intent_snapshots, self.player.native_saves):
            self.assertEqual(snapshot['pending']['label'], 'save_native_' + request['provider'] + '_' + str(request['number']))
            self.assertEqual(set(snapshot['pending']), {'label', 'key', 'safe_after'})
        private = self.path.read_text() + json.dumps(self.player.intent_snapshots)
        for forbidden in (PRIVATE, presets.PLAYLIST, presets.TOKEN, 'revision-', 'context-', 'runtime-native', 'settings'):
            self.assertNotIn(forbidden, private)

    def test_resume_lost_applied_save_compares_and_skips_consumed_receipt(self):
        self.player.native_loss = 'after'
        self.assertEqual(self.run_session()['status'], 'failed')
        first = copy.deepcopy(self.player.native_saves[0])
        self.assertIsNotNone(json.loads(self.path.read_text())['pending'])
        self.assertEqual(self.run_session(True)['status'], 'loaded')
        self.assertEqual(len([row for row in self.player.native_saves if row['provider'] == first['provider']]), 1)
        self.assertNotIn(first['revision'], self.player.tickets)
        self.assertEqual(len(self.player.native_saves), 2)

    def test_unknown_resume_waits_ttl_then_uses_new_key_and_new_tokens(self):
        self.player.native_loss = 'before'
        self.assertEqual(self.run_session()['status'], 'failed')
        pending = json.loads(self.path.read_text())['pending']
        count = len(self.player.calls)
        with self.assertRaisesRegex(ott.Error, 'uncertain outcome'):
            self.run_session(True)
        self.assertEqual(len(self.player.calls), count)
        with mock.patch.object(session.time, 'time', return_value=pending['safe_after'] + 1):
            self.assertEqual(self.run_session(True)['status'], 'loaded')
        writes = [(params, key) for action, params, key in self.player.bound_calls if action == 'provider_settings']
        self.assertNotEqual(writes[0][1], writes[1][1])
        self.assertNotEqual(writes[0][0]['revision'], writes[1][0]['revision'])

    def test_cancelled_preflight_and_expired_compare_send_no_save(self):
        self.player.group_cancelled = threading.Event()
        self.player.group_cancelled.set()
        self.assertEqual(self.load()['status'], 'interrupted')
        self.assertEqual(self.player.calls, [])
        self.player.group_cancelled.clear()
        clock = [100.0]
        def expired(action, params):
            if action == 'settings_compare':
                clock[0] += 46
        self.player.native_fault = expired
        with mock.patch.object(time, 'monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(self.load()['status'], 'failed')
        self.assertEqual(self.player.native_saves, [])

    def test_journal_persistence_cannot_extend_write_deadline_or_ignore_cancellation(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                self.player = NativePlayer()
                self.player.group_cancelled = threading.Event()
                checkpoint = Path(self.temp.name) / ('cancel' if cancelled else 'deadline')
                current = session.Session(ott, self.player, 'dev_tv', self.name, self.preset, checkpoint, False)
                clock = [100.0]
                original = current.retry
                def delayed_intent(*args):
                    original(*args)
                    if cancelled:
                        self.player.group_cancelled.set()
                    else:
                        clock[0] += 46
                try:
                    with mock.patch.object(current, 'retry', side_effect=delayed_intent), \
                            mock.patch.object(time, 'monotonic', side_effect=lambda: clock[0]):
                        result = self.load(current)
                    self.assertEqual(result['status'], 'interrupted' if cancelled else 'failed')
                    self.assertEqual(self.player.native_saves, [])
                finally:
                    current.close()

    def test_full_registry_checkpoint_stays_bounded_and_resumable(self):
        rows = [{'provider': provider, 'number': number,
                 'settings': {'key': PRIVATE} if provider == 'edem' else {'password': PRIVATE}}
                for provider in FIXTURE['provider_ids'] for number in range(1, 16)]
        _, self.preset = ott.validate_preset(configuration(rows), 'local')
        current = session.Session(ott, self.player, 'dev_tv', self.name, self.preset, self.path, False)
        try:
            current.state['completed'] = ['x' * 64] * 760
            current.save()
        finally:
            current.close()
        self.assertLess(self.path.stat().st_size, 65536)
        current = session.Session(ott, self.player, 'dev_tv', self.name, self.preset, self.path, True)
        try:
            current.state['completed'].append('one-too-many')
            current.save()
        finally:
            current.close()
        with self.assertRaises(ott.Error):
            session.Session(ott, self.player, 'dev_tv', self.name, self.preset, self.path, True)


if __name__ == '__main__':
    unittest.main()
