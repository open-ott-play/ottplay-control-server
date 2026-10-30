"""Torrent commands share normal target/group routing and fail before dispatch."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_torrent_routing', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class TorrentRoutingTest(unittest.TestCase):
    def invoke(self, words, config=None, client=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value=config or {}) as read, \
                mock.patch.object(ott, 'Client', return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), read.call_count

    def test_help_without_credentials_or_player(self):
        code, out, err, reads = self.invoke(['t1', 'torrent', '--help'])
        self.assertEqual((code, err, reads), (0, '', 0))
        for command in ('list', 'inspect', 'play', 'export', 'import', 'cancel'):
            self.assertIn(command, out)

    def test_invalid_arguments_fail_before_loading_credentials(self):
        for tail in (['delete'], ['play', 'entry', '-1'], ['add', 'https://example.org/file.mp4'],
                     ['export', 'file.json', '--surprise'], ['status', 'not-an-operation']):
            with self.subTest(tail=tail):
                code, out, err, reads = self.invoke(['t1,t2', 'torrent', *tail])
                self.assertEqual((code, out, reads), (1, '', 0))
                self.assertIn('Use torrent', err)

    def test_extended_timeout_is_torrent_only(self):
        for words in (['-t', '10801', 't1', 'torrent', 'list'], ['-t', '301', 't1', 'status']):
            code, out, err, reads = self.invoke(words)
            self.assertEqual((code, out, reads), (1, '', 0))
            self.assertIn('--timeout', err)

    def test_torrent_name_remains_available_to_management_commands(self):
        with mock.patch.object(ott, 'registry_management') as management:
            code, out, err, reads = self.invoke(['unalias', 'torrent'])
        self.assertEqual((code, out, err, reads), (0, '', '', 1))
        self.assertEqual(management.call_args.args[2], ['unalias', 'torrent'])

    def test_marker_requires_exact_versioned_capability(self):
        base = {'version': 1, 'player': {'version': 'test', 'platform': 'tauri', 'runtime': 'runtime'},
                'lifecycle': [], 'input': [], 'playback': []}
        self.assertNotIn('torrent_catalog', ott.capabilities_metadata(base))
        good = dict(base, torrent_catalog={'version': 1})
        self.assertEqual(ott.capabilities_metadata(good)['torrent_catalog'], {'version': 1})
        for marker in ({'version': True}, {'version': 2}, {'version': 1, 'extra': True}, None):
            with self.subTest(marker=marker), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(base, torrent_catalog=marker))

    def test_group_routes_each_target_with_separate_export_path(self):
        config = {'players': {'t1': 'dev1', 't2': 'dev2'}}
        client = SimpleNamespace(config=config, server='https://controller.example', timeout=10800, on_receipt=None,
                                 credentials={'devices': [{'id': value} for value in config['players'].values()]},
                                 device=lambda name: config['players'][name])
        module = ott.torrent_module()
        calls = []
        def run(api, current, device, words, timeout, as_json, path):
            calls.append((device, words, timeout, as_json))
            print(json.dumps({'status': 'exported'}))
            return 0
        module.run = run
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ott, 'torrent_module', return_value=module):
            destination = str(Path(directory) / 'catalog.json')
            code, out, err, _ = self.invoke(['-j', '-t', '10800', 't1,t2', 'torrent', 'export',
                                             destination, '--with-favorites'], config, client)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['succeeded'], 2)
        self.assertEqual([row[0] for row in calls], ['dev1', 'dev2'])
        self.assertEqual([Path(row[1][1]).name for row in calls], ['catalog.t1.json', 'catalog.t2.json'])
        self.assertTrue(all(row[2:] == (10800, True) for row in calls))


if __name__ == '__main__':
    unittest.main()
