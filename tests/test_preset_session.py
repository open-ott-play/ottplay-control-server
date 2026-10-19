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

    def test_legacy_vportal_does_not_change_revision_or_exact_comparisons(self):
        legacy = config()
        for row in legacy['presets']['local']['m3u']:
            row['vportal'] = 'retired private portal'
        _, normalized = ott.validate_preset(legacy, 'local')
        self.assertEqual(session.digest(normalized), session.digest(self.preset))
        self.assertEqual(list(session.comparisons(normalized)), list(session.comparisons(self.preset)))
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
