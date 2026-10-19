import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_offline_profiles', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


def document():
    return {'version': 1, 'provider': 'offline', 'name': 'Movies',
            'binding_id': 'c98c115d-1234-4567-8abc-123456789abc',
            'sources': [{'key': 's0', 'name': 'Мультфильмы', 'kind': 'folder'}],
            'selected': {'source_key': 's0', 'name': 'Винни-Пух.mp4'}}


def row(number=1, active=True, binding=False):
    return {'number': number, 'name': 'Movies', 'active': active,
            'configured': True, 'binding_required': binding, 'source_count': 1,
            'selected_name': 'Винни-Пух.mp4'}


def caps(operations=None):
    return {'version': 1, 'player': {'version': '1.2.3', 'platform': 'browser', 'runtime': 'runtime-1'},
            'lifecycle': [], 'input': [], 'playback': [],
            'offline_profiles': {'version': 1, 'operations': operations if operations is not None else ['list', 'load', 'save', 'export', 'import'],
                                 'max_profiles': 15, 'max_sources': 32}}


class OfflineProfileTest(unittest.TestCase):
    def run_cli(self, words, responses, json_output=False):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = responses
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = ott.main((['-j'] if json_output else []) + ['tv'] + words)
        return status, output.getvalue(), errors.getvalue(), client

    def test_parser_compatible_commands(self):
        for words, result in [
            (['profile', 'save', '1'], ('profile_save', {'number': 1})),
            (['prof', 'SAVE', '15', 'Local', 'videos'], ('profile_save', {'number': 15, 'name': 'Local videos'})),
            (['profile', 'load', '1'], ('profile', {'number': 1})),
            (['profile', '1'], ('profile', {'number': 1})),
            (['profile', 'export', '2', 'movies.json'], ('profile_export', {'number': 2, 'output': 'movies.json'})),
            (['provider', 'offline'], ('provider', {'query': 'offline'})),
        ]:
            self.assertEqual(ott.parse_command(words), result)
        for words in [['profile', 'save'], ['profile', 'load', '1', 'oops'], ['profile', 'export', '1'],
                      ['profile', 'import', '1'], ['profile', 'save', '0'], ['profile', 'load', '01']]:
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(words)

    def test_import_validates_before_any_rpc(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'profile.json'
            target.write_text(json.dumps(document()))
            self.assertEqual(ott.parse_command(['profile', 'import', '3', str(target)]),
                             ('profile_import', {'number': 3, 'profile': document()}))
            for content in ['{"version":1,"version":1}', '{"version":NaN}', '[]', '\ud800', ' ' * 16385]:
                target.write_text(content, errors='surrogatepass')
                status, output, error, client = self.run_cli(['profile', 'import', '3', str(target)], [])
                self.assertEqual((status, output, client.call.call_count), (1, '', 0))
                self.assertTrue(error.startswith('Error:'))

    def test_document_rejects_authority_unknown_fields_and_invalid_references(self):
        invalid = []
        for key in ['url', 'path', 'handle', 'bookmark', 'nativeRef', 'token', 'data', 'files']:
            value = document(); value[key] = 'private'; invalid.append(value)
            value = document(); value['sources'][0][key] = 'private'; invalid.append(value)
            value = document(); value['selected'][key] = 'private'; invalid.append(value)
        for key, value in [('version', True), ('version', 1.0), ('provider', 'm3u'), ('name', 'я' * 129),
                           ('binding_id', 'native-source-1'), ('sources', None), ('selected', {'source_key': 's1', 'name': 'a'})]:
            bad = document(); bad[key] = value; invalid.append(bad)
        for key, value in [('key', 's1'), ('kind', 'server'), ('name', ''), ('name', 'a\n'), ('name', '\ud800'), ('name', 'я' * 257)]:
            bad = document(); bad['sources'][0][key] = value; invalid.append(bad)
        bad = document(); bad['sources'] *= 2; invalid.append(bad)
        bad = document(); bad['sources'] = [{'key': f's{i}', 'name': 'a', 'kind': 'file'} for i in range(33)]; invalid.append(bad)
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(ott.Error):
                ott.offline_profile_document(bad)
        empty = document(); empty.update(sources=[], selected=None)
        self.assertEqual(ott.offline_profile_document(empty), empty)
        maximum = document(); maximum['sources'] = [{'key': f's{i}', 'name': 'a' * 512, 'kind': 'folder'} for i in range(32)]
        with self.assertRaisesRegex(ott.Error, '12 KiB'):
            ott.offline_profile_document(maximum)

    def test_save_and_cross_device_import_receipts(self):
        receipt = {'provider': 'offline', 'profile': row(), 'saved': True, 'binding_required': False}
        status, output, error, client = self.run_cli(['profile', 'save', '1', 'Movies'], [caps(), receipt])
        self.assertEqual((status, error), (0, ''))
        self.assertIn('Playback was not started', output)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {}),
                                                    mock.call('dev_tv', 'profile_save', {'number': 1, 'name': 'Movies'})])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'profile.json'; target.write_text(json.dumps(document()))
            receipt.update(profile=row(binding=True), binding_required=True)
            status, output, error, client = self.run_cli(['profile', 'import', '1', str(target)], [caps(), receipt])
            self.assertEqual((status, error), (0, ''))
            self.assertIn('Local binding required', output)
            self.assertEqual(client.call.call_args_list[-1], mock.call('dev_tv', 'profile_import', {'number': 1, 'profile': document()}))

    def test_export_is_validated_atomic_private_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'export.json'
            response = {'provider': 'offline', 'number': 1, 'profile': document()}
            status, output, error, client = self.run_cli(['profile', 'export', '1', str(target)], [caps(), response], True)
            self.assertEqual((status, error), (0, ''))
            self.assertEqual(json.loads(target.read_text()), document())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output)['path'], str(target.resolve()))
            self.assertEqual(client.call.call_args_list[-1], mock.call('dev_tv', 'profile_export', {'number': 1}))
            original = target.read_bytes()
            status, output, error, _ = self.run_cli(['profile', 'export', '1', str(target)], [caps(), response])
            self.assertEqual((status, output), (1, ''))
            self.assertIn('already exists', error)
            self.assertEqual(target.read_bytes(), original)
            target.unlink(); response['profile']['bookmark'] = 'secret'
            status, output, error, _ = self.run_cli(['profile', 'export', '1', str(target)], [caps(), response])
            self.assertEqual((status, output, target.exists()), (1, '', False))
            self.assertNotIn('secret', error)

    def test_capabilities_and_mutation_uncertainty_never_retry(self):
        for responses, count in [([caps([])], 1), ([caps(), ott.PlayerRejected('secret-url', {})], 2),
                                 ([caps(), ott.TransportError('uncertain')], 2),
                                 ([caps(), {'provider': 'offline', 'profile': row(), 'saved': True, 'binding_required': True}], 2)]:
            status, output, error, client = self.run_cli(['profile', 'save', '1'], responses)
            self.assertEqual((status, output, client.call.call_count), (1, '', count))
            self.assertNotIn('secret-url', error)
            self.assertEqual(client.timeout, 45)
        for bad in [dict(caps()['offline_profiles'], max_sources=True), dict(caps()['offline_profiles'], operations=['save', 'save'])]:
            value = caps(); value['offline_profiles'] = bad
            with self.assertRaises(ott.Error): ott.capabilities_metadata(value)

    def test_null_offline_capability_is_absent_for_other_providers(self):
        data = caps()
        data['offline_profiles'] = None
        self.assertNotIn('offline_profiles', ott.capabilities_metadata(data))
        status, output, error, _ = self.run_cli([], [{'provider': 'm3u'}, data])
        self.assertEqual((status, error), (0, ''))
        self.assertIn('Available controls now', output)
        self.assertIn('provider offline', output)

    def test_path_descriptions_match_player_validation(self):
        for name in ['../video.mp4', '/video.mp4', 'a//b', 'a/', 'file:foo', 'C:\\movie.mp4']:
            bad = document(); bad['selected']['name'] = name
            with self.subTest(name=name), self.assertRaises(ott.Error):
                ott.offline_profile_document(bad)
        for name in ['.', '..', 'file:foo', 'parent/folder', 'C:\\folder']:
            bad = document(); bad['sources'][0]['name'] = name
            with self.subTest(name=name), self.assertRaises(ott.Error):
                ott.offline_profile_document(bad)

    def test_new_library_and_saved_before_load_can_have_no_active_slot(self):
        listing = {'provider': 'offline', 'profiles': [row(i, False) for i in range(1, 16)]}
        for current in listing['profiles']:
            current.update(name='', configured=False, binding_required=False, source_count=0, selected_name='')
        self.assertEqual(ott.profile_metadata(listing, 'profiles', {}), listing)
        listing['profiles'][0] = row(1, False)
        self.assertEqual(ott.profile_metadata(listing, 'profiles', {}), listing)
        listing['profiles'][0]['active'] = True
        listing['profiles'][1]['active'] = True
        with self.assertRaises(ott.Error):
            ott.profile_metadata(listing, 'profiles', {})

    def test_offline_listing_load_and_help(self):
        listing = {'provider': 'offline', 'profiles': [row(i, i == 1, True) for i in range(1, 16)]}
        status, output, error, _ = self.run_cli(['profiles'], [listing])
        self.assertEqual((status, error), (0, ''))
        self.assertIn('binding required', output)
        status, output, error, _ = self.run_cli(['profile', 'load', '1'], [
            {'provider': 'offline', 'profile': row(binding=True), 'dispatched': True, 'binding_required': True}])
        self.assertEqual((status, error), (0, ''))
        self.assertIn('Playback has not been confirmed', output)
        status, output, error, _ = self.run_cli([], [{'provider': 'offline'}, caps()])
        self.assertEqual((status, error), (0, ''))
        for command in ['profile save 1 Movies', 'profile load 1', 'profile export 1 movies.json', 'profile import 2 movies.json']:
            self.assertIn(command, output)
        status, output, error, _ = self.run_cli([], [{'provider': 'offline', 'kiosk': {'enabled': True}}, caps(['list', 'export'])])
        self.assertEqual((status, error), (0, ''))
        self.assertNotIn('profile save', output)
        self.assertIn('profile export', output)


if __name__ == '__main__':
    unittest.main()
