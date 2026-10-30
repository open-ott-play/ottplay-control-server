"""Saved group management and dispatch, without contacting real players."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('existing_group_fixture', Path(__file__).with_name('test_player_groups_cli.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
ott = fixture.ott


class NamedGroupsTest(unittest.TestCase):
    def setUp(self):
        self.helper = fixture.PlayerGroupsTest()
        self.client = self.helper.client()
        self.client.config['groups'] = {'tt': ['t1', 't2', 't3', 't4']}
        self.client.call.side_effect = lambda device, action, params: (
            {'dispatched': True, 'provider': 'm3u'} if action == 'provider'
            else self.helper.reply(device, action, params))

    def run_cli(self, words):
        return self.helper.run_cli(words, self.client)

    def test_saved_group_runs_existing_commands_in_order(self):
        for command in (['R'], ['v', '35'], ['provider', 'm3u'], ['standby'], ['restart', 'stream']):
            with self.subTest(command=command):
                self.client.call.reset_mock()
                code, out, err, _ = self.run_cli(['-j', 'TT', *command])
                self.assertEqual((code, err), (0, ''))
                result = json.loads(out)
                self.assertEqual((result['succeeded'], result['failed']), (4, 0))
                self.assertEqual([row['player'] for row in result['results']], ['t1', 't2', 't3', 't4'])

    def test_mixed_groups_and_aliases_deduplicate_device_identity(self):
        self.client.config['players']['other'] = 'dev_2'
        self.client.config['groups']['one'] = ['other', 't3']
        code, out, err, _ = self.run_cli(['-j', 'one,tt,t1', 'v', '35'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual([row['player'] for row in json.loads(out)['results']], ['other', 't3', 't1', 't4'])
        self.assertEqual(self.client.call.call_count, 4)

    def test_all_members_resolve_before_first_command(self):
        self.client.config['groups']['tt'] = ['t1', 'missing']
        code, _, err, _ = self.run_cli(['tt', 'R'])
        self.assertEqual(code, 1)
        self.assertIn('Unknown player', err)
        self.client.call.assert_not_called()

    def test_invalid_definitions_fail_before_dispatch(self):
        for groups, selector in (({'tt': []}, 'tt'), ({'tt': 't1'}, 'tt'),
                                 ({'tt': ['tt']}, 'tt'), ({'tt': ['t1'], 'TT': ['t2']}, 'tt'),
                                 ({'t1': ['t2']}, 't1'), ({'tt': ['t1'] * 64}, 'tt,t2')):
            with self.subTest(groups=groups):
                self.client.config['groups'] = groups
                code, _, _, _ = self.run_cli([selector, 'R'])
                self.assertEqual(code, 1)
                self.client.call.assert_not_called()

    def test_load_uses_group_route_instead_of_single_player_fast_path(self):
        args = ['tt', 'load', 'Home']
        with mock.patch.object(ott, 'parse_preset_load'), mock.patch.object(ott, 'preset_command') as single, \
                mock.patch.object(ott, 'player_group_command', return_value=0) as grouped:
            code, _, err, _ = self.run_cli(args)
        self.assertEqual((code, err), (0, ''))
        single.assert_not_called()
        self.assertEqual(grouped.call_args.args[2], [(f't{i}', f'dev_{i}') for i in range(1, 5)])

    def test_watch_resolves_the_same_group(self):
        fleet = ott.fleet_module()
        with mock.patch.object(ott, 'fleet_module', return_value=fleet), \
                mock.patch.object(fleet, 'collect', return_value={'status': 'completed', 'summary': {}}) as collect:
            code, _, err, _ = self.run_cli(['-j', 'tt', 'watch'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(collect.call_args.args[2], [(f't{i}', f'dev_{i}') for i in range(1, 5)])
        self.client.call.assert_not_called()

    def test_alias_creation_cannot_shadow_a_group(self):
        spec = importlib.util.spec_from_file_location('group_registry', Path(ott.__file__).with_name('device_registry.py'))
        registry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(registry)
        with self.assertRaisesRegex(ott.Error, 'belongs to a player group'):
            registry.alias_key(SimpleNamespace(**vars(ott)), self.client.config, 'TT', 'dev_1')


class GroupManagementTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'cli.json'
        self.config = {'players': {'t1': 'dev_1', 't2': 'dev_2', 'alias2': 'dev_2'},
                       'presets': {'Home': {'keep': True}}, 'server_config': '/missing/credentials'}
        ott.write_private(self.path, self.config)

    def run_cli(self, words):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', side_effect=AssertionError('must stay offline')), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(['-c', str(self.path), *words])
        return code, out.getvalue(), err.getvalue()

    def test_create_list_show_replace_remove_preserve_other_config(self):
        self.assertEqual(self.run_cli(['group', 'add', 'tt', 't1,t2', 'alias2'])[0], 0)
        stored = ott.read_json(self.path)
        self.assertEqual(stored, {**self.config, 'groups': {'tt': ['t1', 't2']}})
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        code, out, err = self.run_cli(['groups', '-j'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), {'groups': {'tt': ['t1', 't2']}})
        self.assertEqual(self.run_cli(['group', 'show', 'TT'])[1].strip(), 'tt: t1, t2')
        self.assertEqual(self.run_cli(['group', 'add', 'tt', 't2'])[0], 1)
        self.assertEqual(self.run_cli(['group', 'set', 'TT', 'dev_2'])[0], 0)
        self.assertEqual(ott.read_json(self.path)['groups'], {'tt': ['t2']})
        self.assertEqual(self.run_cli(['group', 'remove', 'tt'])[0], 0)
        self.assertEqual(ott.read_json(self.path), {**self.config, 'groups': {}})

    def test_invalid_inputs_leave_config_unchanged(self):
        before = self.path.read_bytes()
        for words in (['add', 'tt', 'missing'], ['add', 'tt', 't1,,t2'], ['add', 't1', 't2'],
                      ['add', 'dev_1', 't2'], ['add', 'group', 't1'], ['set', 'bad name', 't1'],
                      ['set', 'tt'], ['remove', 'missing'], ['show', 'missing'], ['list', 'extra']):
            with self.subTest(words=words):
                self.assertEqual(self.run_cli(['group', *words])[0], 1)
                self.assertEqual(self.path.read_bytes(), before)

    def test_help_needs_no_configuration_or_credentials(self):
        self.path.unlink()
        code, out, err = self.run_cli(['group', '--help'])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('ott group set tt t1,t2,t3,t4', out)

    def test_changed_configuration_is_not_overwritten(self):
        with mock.patch.object(ott, 'read_json', side_effect=[self.config, {**self.config, 'new': True}]), \
                mock.patch.object(ott, 'write_private') as write:
            code, _, err = self.run_cli(['group', 'set', 'tt', 't1'])
        self.assertEqual(code, 1)
        self.assertIn('configuration changed', err)
        write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
