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

from test_presets_cli import FakePlayer, config, ott, TOKEN, PLAYLIST

spec = importlib.util.spec_from_file_location('preset_session', Path(__file__).resolve().parents[1] / 'cli/preset_session.py')
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class ExactPlayer(FakePlayer):
    server = 'https://controller.example'

    def __init__(self):
        super().__init__()
        self.exact = {}
        self.operations = {}
        self.fail_after_write = False

    def api(self, path):
        if '&key=' in path:
            return 200, self.operations.get(path.split('&key=')[1], {'state': 'unknown'})
        return 200, {'enabled': True, 'command_ttl_seconds': 60}

    def call(self, device, action, params, idempotency_key=None):
        if action == 'settings_compare':
            key = params['provider'], params.get('number')
            return {'provider': key[0], **({'number': key[1]} if key[1] is not None else {}),
                    'matched': self.exact.get(key) == params['settings']}
        data = super().call(device, action, params)
        if action == 'profile_settings':
            self.exact['m3u', params['number']] = copy.deepcopy(params['settings'])
        elif action == 'provider_settings':
            row = params['settings']
            self.exact[params['provider'], row.get('profile')] = {k: v for k, v in row.items() if k != 'profile'}
        if idempotency_key:
            self.operations[idempotency_key] = {'state': 'responded', 'result_status': 'ok'}
        if action == 'profile_settings' and self.fail_after_write:
            self.fail_after_write = False
            raise ott.Error('response lost')
        return data


class PresetVerificationTests(unittest.TestCase):
    def setUp(self):
        self.config = config()
        self.config['presets']['local']['local'] = {'sources': [{'name': 'Movies'}]}
        _, self.preset = ott.validate_preset(self.config, 'local')
        self.player = ExactPlayer()
        self.player.active = self.preset['active_profile']
        for _, params in session.comparisons(self.preset):
            if params['provider'] != 'local':
                self.player.exact[params['provider'], params.get('number')] = params['settings']
        local_patch = mock.patch.object(ott, 'local_preset_matches', return_value=True)
        self.local = local_patch.start()
        self.addCleanup(local_patch.stop)

    def verify(self):
        with mock.patch.object(self.player, 'call', wraps=self.player.call) as calls:
            result = session.verify(ott, self.player, 'dev_tv', self.preset)
        self.assertTrue(all(call.args[1] in {'settings_compare', 'status', 'profiles'}
                            for call in calls.call_args_list))
        self.assertEqual([row['step'] for row in result['comparisons']],
                         [name for name, _ in session.comparisons(self.preset)])
        self.assertEqual(result['expected_provider'], 'm3u')
        self.assertEqual(result['expected_active_profile'], self.preset['active_profile'])
        return result

    def profile_reply(self, provider):
        if provider == 'offline':
            rows = [{'number': n, 'name': '', 'active': False, 'configured': False,
                     'binding_required': False, 'source_count': 0, 'selected_name': ''}
                    for n in range(1, 16)]
        else:
            rows = [dict(self.player.metadata(n), vportal_configured=False) for n in range(1, 16)]
        return {'provider': provider, 'profiles': rows}

    def test_m3u_matching_selection_is_verified(self):
        result = self.verify()
        self.assertEqual((result['status'], result['provider'], result['active_profile']),
                         ('verified', 'm3u', 1))
        self.assertEqual([action for _, action, _ in self.player.calls], ['status', 'profiles'])

    def test_m3u_wrong_slot_is_different(self):
        self.player.active = 2
        result = self.verify()
        self.assertEqual((result['status'], result['provider'], result['active_profile']),
                         ('different', 'm3u', 2))

    def test_other_providers_preserve_comparisons_without_requesting_profiles(self):
        for provider in ['offline', 'vportal', 'plex', 'stalker']:
            with self.subTest(provider=provider):
                self.player.provider = provider
                self.player.calls.clear()
                self.player.faults[('profiles', 1)] = AssertionError('Profiles must not be requested')
                result = self.verify()
                self.assertEqual((result['status'], result['provider'], result['active_profile']),
                                 ('different', provider, None))
                self.assertTrue(all(row['matched'] for row in result['comparisons']))
                self.assertEqual([action for _, action, _ in self.player.calls], ['status'])

    def test_setting_mismatch_is_preserved_with_correct_m3u_selection(self):
        self.local.return_value = False
        result = self.verify()
        self.assertEqual(result['status'], 'different')
        self.assertEqual(result['comparisons'][0], {'step': 'local', 'matched': False})
        self.assertEqual(result['active_profile'], 1)

    def test_malformed_provider_status_remains_an_error(self):
        for status in [None, [], {}, {'provider': None}, {'provider': True}, {'provider': ''},
                       {'provider': 'm3u\n'}, {'provider': 'https://private.example'},
                       {'provider': 'x' * 33}]:
            with self.subTest(status=status):
                self.player.counts.clear()
                self.player.calls.clear()
                self.player.faults[('status', 1)] = status
                with self.assertRaisesRegex(ott.Error, 'invalid provider status'):
                    self.verify()
                self.assertEqual([action for _, action, _ in self.player.calls], ['status'])

    def test_provider_changed_before_profiles_cannot_confirm_a_matching_slot(self):
        for provider in ['offline', 'vportal']:
            with self.subTest(provider=provider):
                self.player.counts.clear()
                self.player.faults[('profiles', 1)] = self.profile_reply(provider)
                result = self.verify()
                self.assertEqual((result['status'], result['provider'], result['active_profile']),
                                 ('different', provider, None))
                self.assertTrue(all(row['matched'] for row in result['comparisons']))

    def test_malformed_profile_metadata_remains_an_error(self):
        malformed = [self.profile_reply('m3u'), self.profile_reply('m3u'), self.profile_reply('vportal')]
        malformed[0]['profiles'].pop()
        malformed[1]['profiles'][0]['active'] = 'true'
        malformed[2]['profiles'][0]['vportal_configured'] = 'false'
        for reply in malformed + [{'provider': 'plex', 'profiles': []}]:
            with self.subTest(provider=reply['provider']):
                self.player.counts.clear()
                self.player.faults[('profiles', 1)] = reply
                with self.assertRaisesRegex(ott.Error, 'invalid profile metadata'):
                    self.verify()

    def test_verify_command_reports_difference_and_retains_local_comparison(self):
        self.player.provider = 'offline'
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = session.run(ott, self.config, ['tv', 'load', 'local', 'verify'], 45,
                               True, None, client=self.player, device='dev_tv')
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['provider'], 'offline')
        self.assertIsNone(result['active_profile'])
        self.assertEqual(result['comparisons'][0], {'step': 'local', 'matched': True})
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertNotIn(PLAYLIST, output.getvalue())


class PresetSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'checkpoint.json'
        self.name, self.preset = ott.validate_preset(config(), 'local')
        self.player = ExactPlayer()

    def run_session(self, resume=False):
        current = session.Session(ott, self.player, 'dev_tv', self.name, self.preset, self.path, resume)
        try:
            return ott.load_preset(self.player, 'dev_tv', self.name, self.preset, session=current)
        finally:
            current.close()

    def test_lost_reply_resume_compares_before_rewriting_and_checkpoint_has_no_secrets(self):
        self.player.fail_after_write = True
        self.assertEqual(self.run_session()['status'], 'failed')
        self.assertEqual(self.run_session(True)['status'], 'loaded')
        self.assertEqual(sum(number == 1 for number, _ in self.player.saved), 1)
        self.assertEqual(session.verify(ott, self.player, 'dev_tv', self.preset)['status'], 'verified')
        text = self.path.read_text()
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(PLAYLIST, text)
        # Windows stat mode bits do not describe the inherited directory ACL.
        # The recovery/content checks above still run on every platform.
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_completed_steps_are_rechecked_after_drift(self):
        self.assertEqual(self.run_session()['status'], 'loaded')
        self.player.exact['m3u', 1]['playlist'] = 'https://other.example'
        self.assertEqual(self.run_session(True)['status'], 'loaded')
        self.assertEqual(sum(number == 1 for number, _ in self.player.saved), 2)

    def test_unknown_outcome_blocks_all_mutations_until_deadline(self):
        current = session.Session(ott, self.player, 'dev_tv', self.name, self.preset, self.path, False)
        current.retry('select_m3u', 'provider', {'query': 'm3u'})
        current.close()
        with self.assertRaisesRegex(ott.Error, 'uncertain outcome'):
            self.run_session(True)
        self.assertEqual(self.player.calls, [])
        future = time.time() + 200
        with mock.patch.object(session.time, 'time', return_value=future):
            self.assertEqual(self.run_session(True)['status'], 'loaded')

    def test_revision_mismatch_and_concurrent_writer_fail_closed(self):
        current = session.Session(ott, self.player, 'dev_tv', self.name, self.preset, self.path, False)
        try:
            with self.assertRaises(OSError):
                self.run_session(True)
        finally:
            current.close()
        self.preset['active_profile'] = 2
        with self.assertRaisesRegex(ott.Error, 'revision'):
            self.run_session(True)
        self.assertEqual(self.player.calls, [])

    def test_plan_is_offline_and_does_not_disclose_config(self):
        out = io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=self.player), mock.patch.object(self.player, 'api', side_effect=AssertionError('network')), contextlib.redirect_stdout(out):
            self.assertEqual(session.run(ott, config(), ['tv','load','local','plan'],45,True,None),0)
        self.assertNotIn(TOKEN,out.getvalue())
        self.assertEqual(self.player.calls,[])

    def test_legacy_vportal_changes_revision_and_adds_exact_comparisons(self):
        legacy = config()
        for row in legacy['presets']['local']['m3u']:
            row['vportal'] = 'retired private portal'
        _, normalized = ott.validate_preset(legacy, 'local')
        self.assertNotEqual(session.digest(normalized), session.digest(self.preset))
        self.assertEqual([name for name, _ in session.comparisons(normalized) if name.startswith('vportal_')],
                         ['vportal_1', 'vportal_2'])
        for provider, params in session.comparisons(normalized):
            if provider.startswith('m3u_'):
                self.assertEqual(set(params['settings']), {'name', 'playlist', 'history_hours'})

    def test_checkpoint_from_legacy_writer_is_not_silently_migrated(self):
        old_preset = copy.deepcopy(self.preset)
        for row in old_preset['m3u']:
            row['settings']['vportal'] = 'old private portal'
        current = session.Session(ott, self.player, 'dev_tv', self.name, old_preset, self.path, False)
        current.retry('save_profile_1', 'profile_settings', old_preset['m3u'][0])
        current.close()
        with self.assertRaisesRegex(ott.Error, 'revision'):
            self.run_session(True)
        self.assertEqual(self.player.calls, [])

    def test_old_player_exact_compare_fails_before_mutation(self):
        self.player.call = mock.Mock(side_effect=ott.PlayerUnsupported('unsupported', {}))
        with mock.patch.object(ott,'Client',return_value=self.player), self.assertRaises(ott.PlayerUnsupported):
            session.run(ott, config(), ['tv','load','local','apply','--checkpoint',str(self.path)],45,True,None)
        self.assertEqual(self.player.call.call_args.args[1], 'settings_compare')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink permissions')
    def test_symlink_checkpoint_refused(self):
        target = Path(self.temp.name) / 'other'
        target.write_text('do not replace')
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ott.Error, 'regular private'):
            self.run_session()
        self.assertEqual(target.read_text(),'do not replace')
