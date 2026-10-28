"""Explicit settings-only loading preserves favorites and checkpoint intent."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_player_groups_cli as groups
import test_preset_session as sessions
from test_presets_cli import TOKEN, FakePlayer, config, ott, run


def with_favorites():
    value = config()
    value['preset_favorites'] = {'version': 1, 'presets': {'local': {'private': 'do-not-read-or-change'}}}
    return value


class SettingsOnlyTests(unittest.TestCase):
    def test_one_parser_preserves_legacy_shape_and_validates_explicit_flags(self):
        self.assertEqual(ott.parse_command(['load', 'local']), ('load', {'preset': 'local'}))
        for mode in ([], ['plan'], ['apply'], ['resume'], ['verify']):
            for suffix in (mode + ['--settings-only'], ['--settings-only'] + mode):
                parsed = ott.parse_preset_load(['load', 'local', *suffix])
                self.assertTrue(parsed['settings_only'])
                self.assertEqual(parsed['mode'], mode[0] if mode else None)
        for suffix in (['--checkpoint', 'state.json', 'apply', '--settings-only'],
                       ['apply', '--settings-only', '--checkpoint', 'state.json']):
            self.assertEqual(ott.parse_preset_load(['load', 'local', *suffix]),
                             {'preset': 'local', 'mode': 'apply', 'checkpoint': 'state.json', 'settings_only': True})
        bad = [[], ['--settings-only', '--settings-only'], ['--settings-only=yes'], ['--unknown'],
               ['apply', 'verify'], ['--checkpoint', 'x'], ['apply', '--checkpoint'],
               ['apply', '--checkpoint', '--settings-only'],
               ['apply', '--checkpoint', 'a', '--checkpoint', 'b']]
        for suffix in bad:
            words = ['load'] if not suffix else ['load', 'local', *suffix]
            for target in ('tv', 't1,t2'):
                with self.subTest(words=words, target=target):
                    with mock.patch.object(ott, 'read_json', return_value=with_favorites()), \
                            mock.patch.object(ott, 'Client') as client, contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(ott.main([target, *words]), 1)
                    client.assert_not_called()

    def test_legacy_pipeline_is_identical_except_truthful_scope_result(self):
        baseline = FakePlayer()
        old_code, old_out, _, _, _ = run(player=baseline)
        cfg = with_favorites()
        before = copy.deepcopy(cfg)
        current = FakePlayer()
        with mock.patch.object(ott, 'favorites_module', side_effect=AssertionError('no favorites helper')):
            code, out, err, _, _ = run(preset_config=cfg, player=current,
                                      words=['tv', 'load', 'local', '--settings-only'])
        self.assertEqual((old_code, code, err), (0, 0, ''))
        self.assertEqual(current.calls, baseline.calls)
        expected = json.loads(old_out)
        expected.update(settings_only=True, favorites='skipped')
        self.assertEqual(json.loads(out), expected)
        self.assertEqual(cfg, before)
        self.assertNotIn(TOKEN, out)
        code, out, _, _, _ = run(preset_config=cfg, words=['tv', 'load', 'local', '--settings-only'], json_output=False)
        self.assertEqual(code, 0)
        self.assertIn('Preset settings loaded:', out)
        self.assertIn('favorites skipped', out)

    def test_default_all_modes_still_block_before_connection(self):
        cfg = with_favorites()
        for mode in ([], ['plan'], ['apply'], ['resume'], ['verify']):
            with self.subTest(mode=mode):
                code, out, err, player, constructor = run(preset_config=cfg, words=['tv', 'load', 'local', *mode])
                self.assertEqual(code, 1)
                constructor.assert_not_called()
                self.assertEqual(player.calls, [])
                self.assertIn('--settings-only', out + err)

    def test_settings_only_deliberately_ignores_even_unreadable_companion_metadata(self):
        cfg = config()
        cfg['preset_favorites'] = ['future-or-malformed-companion']
        before = copy.deepcopy(cfg)
        code, out, _, _, _ = run(preset_config=cfg, words=['tv', 'load', 'local', '--settings-only'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['favorites'], 'skipped')
        self.assertEqual(cfg, before)

    def test_apply_verify_resume_keep_settings_order_and_local_configuration(self):
        cfg = with_favorites()
        cfg['presets']['local']['local'] = {'sources': [{'name': 'Movies'}]}
        before = copy.deepcopy(cfg)
        player = sessions.ExactPlayer()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            with mock.patch.object(ott, 'favorites_module', side_effect=AssertionError('no favorites helper')), \
                    mock.patch.object(ott, 'local_preset_matches', return_value=True), \
                    mock.patch.object(ott, 'local_preset_plan', return_value=[]):
                for mode in ('plan', 'apply', 'verify', 'resume'):
                    out = io.StringIO()
                    words = ['tv', 'load', 'local', '--settings-only', mode, '--checkpoint', str(path)]
                    with contextlib.redirect_stdout(out):
                        code = sessions.session.run(ott, cfg, words, 45, True, None, client=player, device='dev_tv')
                    result = json.loads(out.getvalue())
                    self.assertEqual(code, 0)
                    self.assertEqual((result['settings_only'], result['favorites']), (True, 'skipped'))
                    if mode == 'plan':
                        self.assertEqual(result['settings'][0], 'local')
                        self.assertFalse(path.exists())
                    if mode in ('verify', 'resume'):
                        self.assertEqual(result['comparisons'][0], {'step': 'local', 'matched': True})
            state = json.loads(path.read_text())
            self.assertIs(state['settings_only'], True)
            self.assertNotIn('do-not-read-or-change', path.read_text())
        self.assertEqual(cfg, before)
        self.assertFalse(any(action.startswith('favorites') for _, action, _ in player.calls))
        self.assertEqual(sum(number == 1 for number, _ in player.saved), 1)

    def test_checkpoints_cannot_cross_modes_and_legacy_shape_is_unchanged(self):
        name, preset = ott.validate_preset(config(), 'local')
        for initial_mode in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'state.json'
                player = sessions.ExactPlayer()
                current = sessions.session.Session(ott, player, 'dev_tv', name, preset, path, False,
                                                   settings_only=initial_mode)
                current.close()
                original = path.read_bytes()
                state = json.loads(original)
                self.assertEqual(set(state), {'version', 'device', 'preset', 'revision', 'server', 'completed', 'pending'}
                                 | ({'settings_only'} if initial_mode else set()))
                self.assertEqual(state['revision'], sessions.session.digest(preset))
                with mock.patch.object(player, 'api', side_effect=AssertionError('mismatch before network')), \
                        self.assertRaisesRegex(ott.Error, 'settings-only mode'):
                    sessions.session.Session(ott, player, 'dev_tv', name, preset, path, True,
                                             settings_only=not initial_mode)
                self.assertEqual(path.read_bytes(), original)
                current = sessions.session.Session(ott, player, 'dev_tv', name, preset, path, True,
                                                   settings_only=initial_mode)
                current.close()
                if initial_mode:
                    state['settings_only'] = 1
                    path.write_text(json.dumps(state))
                    with self.assertRaisesRegex(ott.Error, 'settings-only mode'):
                        sessions.session.Session(ott, player, 'dev_tv', name, preset, path, True, settings_only=True)

    def test_groups_reuse_same_parser_and_expose_skipped_scope(self):
        fixture = groups.PlayerGroupsTest()
        client = fixture.client()
        client.config.update(with_favorites())
        before = copy.deepcopy(client.config)
        for mode in ([], ['plan'], ['verify']):
            with mock.patch.object(groups.ott, 'load_preset', return_value={'status': 'loaded', 'preset': 'local', 'active_profile': 1}), \
                    mock.patch.object(client, 'call', side_effect=self.group_reply):
                code, out, err, _ = fixture.run_cli(['-j', 't1,t2', 'load', 'local', *mode, '--settings-only'], client)
            self.assertEqual((code, err), (0, ''))
            result = json.loads(out)
            self.assertEqual(result['succeeded'], 2)
            self.assertTrue(all(row['data']['favorites'] == 'skipped' for row in result['results']))
        for mode in ('apply', 'resume'):
            words, _, destinations = groups.ott.group_output_arguments(
                ['load', 'local', '--settings-only', mode, '--checkpoint', 'state.json'], 'preset', None, 't1')
            self.assertEqual(words[-1], 'state.t1.json')
            self.assertEqual(destinations[0][1:], (True, mode == 'resume'))
        self.assertEqual(client.config, before)

    def test_group_apply_resume_uses_distinct_scope_bound_checkpoints(self):
        fixture = groups.PlayerGroupsTest()
        client = fixture.client()
        client.config.update(with_favorites())
        players = {device: sessions.ExactPlayer() for device in ('dev_1', 'dev_2')}
        client.call = lambda device, action, params, **options: players[device].call(device, action, params, **options)
        client.api = lambda path: (200, {'enabled': True, 'command_ttl_seconds': 60})
        read_json = groups.ott.read_json
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / 'state.json'
            for mode in ('apply', 'resume'):
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(groups.ott, 'read_json', side_effect=lambda path: client.config
                                       if str(path) == 'fixture-config.json' else read_json(path)), \
                        mock.patch.object(groups.ott, 'Client', return_value=client), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = groups.ott.main(['-c', 'fixture-config.json', '-j', 't1,t2', 'load', 'local', mode,
                                           '--checkpoint', str(checkpoint), '--settings-only'])
                self.assertEqual((code, err.getvalue()), (0, ''), out.getvalue())
                self.assertEqual(json.loads(out.getvalue())['succeeded'], 2)
            self.assertFalse(checkpoint.exists())
            for name, device in (('t1', 'dev_1'), ('t2', 'dev_2')):
                record = json.loads(checkpoint.with_name('state.' + name + '.json').read_text())
                self.assertEqual(record['device'], device)
                self.assertIs(record['settings_only'], True)
                self.assertEqual(sum(number == 1 for number, _ in players[device].saved), 1)

    def test_default_checkpoint_names_separate_explicit_scope_without_changing_legacy_name(self):
        cfg, player = config(), sessions.ExactPlayer()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(sessions.session.Path, 'home', return_value=Path(temporary)):
            for flags in ([], ['--settings-only']):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(sessions.session.run(ott, cfg, ['tv', 'load', 'local', 'apply', *flags],
                                                          45, True, None, client=player, device='dev_tv'), 0)
            folder = Path(temporary) / '.config/ottplay-control/checkpoints'
            paths = {p.name for p in folder.glob('*.json')}
            legacy = sessions.session.digest([player.server, 'dev_tv', 'local']) + '.json'
            explicit = sessions.session.digest([player.server, 'dev_tv', 'local', 'settings-only']) + '.json'
            self.assertEqual(paths, {legacy, explicit})
            self.assertNotIn('settings_only', json.loads((folder / legacy).read_text()))
            self.assertIs(json.loads((folder / explicit).read_text())['settings_only'], True)

    @staticmethod
    def group_reply(device, action, params):
        if action == 'settings_compare':
            return {'provider': params['provider'], 'matched': True,
                    **({'number': params['number']} if 'number' in params else {})}
        if action == 'status':
            return {'provider': 'm3u'}
        if action == 'profiles':
            return {'provider': 'm3u', 'profiles': [FakePlayer(active=1).metadata(n) for n in range(1, 16)]}
        raise AssertionError((device, action, params))


if __name__ == '__main__':
    unittest.main()
