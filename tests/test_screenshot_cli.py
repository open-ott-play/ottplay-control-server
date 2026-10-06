"""Remote image validation, runtime binding and private non-overwriting file saves."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock
import zlib

spec = importlib.util.spec_from_file_location('ott_screenshot', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


def chunk(kind, content):
    return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind + content))


def png(width=2, height=1, pixels=None, depth=8, color=6, interlace=0):
    if pixels is None:
        pixels = b'\x00' + b'\xff\x00\x00\xff' * width
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, interlace))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def caps(state='ready', source='player-view'):
    return {'version': 1, 'player': {'version': '1.1.53-beta.1', 'platform': 'tauri', 'runtime': 'page-123'},
            'lifecycle': [], 'input': [], 'playback': [], 'screenshot': {'state': state, 'source': source}}


def receipt(image=None):
    return {'version': 1, 'runtime': 'page-123', 'mime': 'image/png', 'encoding': 'base64',
            'image': base64.b64encode(png() if image is None else image).decode('ascii'), 'width': 2, 'height': 1,
            'captured_at': 1791264000000, 'source': 'player-view', 'video': 'unknown'}


class ScreenshotCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def run_cli(self, words, replies=None, machine=False, client=None):
        client = client or mock.Mock(timeout=45)
        if not isinstance(client.server, str):
            client.server = 'https://controller.example'
        client.device.return_value = 'dev_tv'
        if replies is not None:
            client.call.side_effect = replies
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                mock.patch.object(ott.Path, 'cwd', return_value=self.directory), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = ott.main((['--json'] if machine else []) + ['tv'] + words)
        return result, output.getvalue(), errors.getvalue(), client

    def test_long_short_and_case_aliases_save_private_unique_png_with_metadata_only(self):
        destinations = []
        for command in ['screenshot', 'shot', 'SCREENSHOT', 'ShOt']:
            for machine in [False, True]:
                code, output, errors, client = self.run_cli([command], [caps(), receipt()], machine)
                self.assertEqual((code, errors), (0, ''))
                self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {}),
                                                             mock.call('dev_tv', 'screenshot', {'runtime': 'page-123'})])
                if machine:
                    metadata = json.loads(output)
                    self.assertNotIn('image', metadata)
                    self.assertNotIn('encoding', metadata)
                    self.assertEqual(metadata['bytes'], len(png()))
                    destination = Path(metadata['path'])
                else:
                    destination = Path(output.split('Screenshot saved: ', 1)[1].split(' (', 1)[0])
                    self.assertIn('video: unknown', output)
                self.assertEqual(destination.read_bytes(), png())
                if os.name != 'nt':
                    self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
                self.assertNotIn(receipt()['image'], output + errors)
                destinations.append(destination)
        self.assertEqual(len(destinations), len(set(destinations)))
        self.assertEqual(list(self.directory.glob('.ott-shot-*')), [])

    def test_explicit_output_forms_and_no_capture_for_invalid_arguments(self):
        for flag in ['-o', '--output']:
            path = self.directory / (flag + '.png')
            code, output, errors, _ = self.run_cli(['shot', flag, str(path)], [caps(), receipt()], True)
            self.assertEqual((code, errors, json.loads(output)['path']), (0, '', str(path)))
        for words in [['shot', '-o'], ['shot', 'file.png'], ['shot', '--output', ''], ['shot', '-o', 'x', '-o', 'y']]:
            code, output, errors, client = self.run_cli(words, [])
            self.assertEqual((code, output, client.call.call_count), (1, '', 0))
            self.assertIn('Use screenshot/shot', errors)
        self.assertEqual(ott.parse_command(['play', 'shot']), ('play', {'query': 'shot'}))

    def test_older_players_source_required_and_unsupported_never_request_capture(self):
        legacy = caps(); del legacy['screenshot']
        for controls in [legacy, caps('unsupported', None), caps('permission_required')]:
            code, output, errors, client = self.run_cli(['shot'], [controls], True)
            self.assertEqual((code, output, client.call.call_count), (1, '', 1))
            self.assertEqual(list(self.directory.iterdir()), [])
            self.assertIn('Select screenshot source in browser' if controls.get('screenshot', {}).get('state') == 'permission_required'
                          else 'not supported', errors)
        for error in [ott.PlayerUnsupported('private-image-data'), ott.PlayerRejected('private-image-data', {})]:
            code, output, errors, client = self.run_cli(['shot'], [caps(), error], True)
            self.assertEqual((code, output, client.call.call_count), (1, '', 2))
            self.assertNotIn('private-image-data', errors)
            self.assertIn('not repeated', errors)
            self.assertIn('current runtime, capture source and OS/adapter support', errors)
            self.assertNotIn('protected settings', errors)
        code, output, errors, client = self.run_cli(['shot'], [caps(), ott.HTTPError(400)])
        self.assertEqual((code, output, client.call.call_count), (1, '', 2))
        self.assertIn('update the controller', errors)

    def test_source_setup_guidance_preserves_legacy_compatibility_without_a_temporary_grant_requirement(self):
        for platform, source in [('browser', None), ('tauri', 'player-view'), ('webos', None)]:
            controls = caps('permission_required', source)
            controls['player']['platform'] = platform
            code, output, errors, client = self.run_cli(['shot'], [controls], True)
            self.assertEqual((code, output, client.call.call_count), (1, '', 1))
            self.assertIn('enabled Remote control connection', errors)
            self.assertIn('In a browser', errors)
            self.assertIn('Older players may still require their local screenshot permission', errors)
            self.assertIn('No capture was requested', errors)
            self.assertNotIn('10 minutes', errors)
            self.assertNotIn('close settings', errors)
            self.assertEqual(list(self.directory.iterdir()), [])

    def test_all_phases_share_deadline_and_restore_client_timeout(self):
        timeouts = []
        client = mock.Mock(timeout=45)
        replies = iter([caps(), receipt()])
        client.call.side_effect = lambda *_: (timeouts.append(client.timeout), next(replies))[1]
        with mock.patch.object(ott.time, 'monotonic', side_effect=[100, 100, 103, 104]):
            code, _, errors, _ = self.run_cli(['shot'], client=client)
        self.assertEqual((code, errors, timeouts, client.timeout), (0, '', [45, 42], 45))
        for clock, replies, expected_calls in [([100, 100, 146], [caps()], 1),
                                               ([100, 100, 101, 146], [caps(), receipt()], 2)]:
            with mock.patch.object(ott.time, 'monotonic', side_effect=clock):
                code, output, errors, client = self.run_cli(['shot'], replies, True)
            self.assertEqual((code, output, client.call.call_count), (1, '', expected_calls))
            self.assertIn('--timeout', errors)

    def test_screenshot_download_requires_https_or_exact_loopback_before_any_work(self):
        for server in ['http://controller.example', 'http://192.168.1.12', 'http://localhost.evil',
                       'http://127.0.0.1.evil', 'http://127.0.0.2', 'http://[2001:db8::1]', 'http://[invalid']:
            client = mock.Mock(timeout=45, server=server)
            with mock.patch.object(ott, 'screenshot_destination') as destination:
                code, output, errors, client = self.run_cli(['shot'], [], client=client)
            self.assertEqual((code, output, client.call.call_count), (1, '', 0))
            self.assertIn('HTTPS controller or HTTP loopback', errors)
            destination.assert_not_called()
        for server in ['https://controller.example', 'http://localhost:8081', 'http://LOCALHOST:8081',
                       'http://127.0.0.1:8081', 'http://[::1]:8081/prefix']:
            client = mock.Mock(timeout=45, server=server)
            code, _, errors, _ = self.run_cli(['shot'], [caps(), receipt()], client=client)
            self.assertEqual((code, errors), (0, ''))
        client = mock.Mock(timeout=45, server='http://192.168.1.12')
        code, _, errors, client = self.run_cli(['caps'], [caps()], client=client)
        self.assertEqual((code, errors, client.call.call_count), (0, '', 1))

    def test_runtime_source_schema_size_and_png_validation_precede_file_creation(self):
        invalid = []
        for key, values in {'runtime': ['page-other', None], 'source': ['display', None], 'version': [True, 2],
                            'width': [True, 0, 3, 1281], 'height': [0, 721, 1.0], 'mime': ['image/jpeg'],
                            'encoding': ['url'], 'captured_at': [None, True, 0, 9007199254740992],
                            'video': ['included'], 'image': ['private', '', 'A' * (1400000), receipt()['image'] + '\n']}.items():
            invalid.extend(dict(receipt(), **{key: value}) for value in values)
        invalid.extend([None, [], {}, dict(receipt(), private='secret'), {k: v for k, v in receipt().items() if k != 'video'},
                        receipt(png() + b'payload'), receipt(png()[:-1]), receipt(png(3)),
                        receipt(png(pixels=b'\x05' + b'\0' * 8)), receipt(png(pixels=b'\0' * 1000000)),
                        receipt(png(pixels=b'\0')), receipt(png(width=1000000000, pixels=b"\0"))])
        for data in invalid:
            with self.subTest(keys=list(data) if isinstance(data, dict) else data):
                code, output, errors, client = self.run_cli(['shot'], [caps(), data], True)
                self.assertEqual((code, output, client.call.call_count), (1, '', 2))
                self.assertIn('invalid screenshot', errors)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_png_validation_rejects_bad_crc_zlib_critical_chunks_and_duplicate_headers(self):
        good = png()
        corrupt = bytearray(good); corrupt[40] ^= 1
        header = good[8:33]
        invalid = [bytes(corrupt), b'JPEG', good[:33] + header + good[33:],
                   good[:33] + chunk(b'EVIL', b'') + good[33:],
                   good[:33] + chunk(b'IDAT', b'not-zlib') + chunk(b'IEND', b''),
                   good[:33] + chunk(b'IDAT', zlib.compress(b'\0' * 9) + b'trailer') + chunk(b'IEND', b'')]
        for image in invalid:
            self.assertFalse(ott.screenshot_png(image, 2, 1))
        # Accept standard grayscale, RGB, RGBA, 16-bit and Adam7 encodings.
        for color, depth, raw in [(0, 1, b'\0\0'), (0, 16, b'\0\0\0'), (2, 8, b'\0\xff\0\0'),
                                   (4, 8, b'\0\xff\xff'), (6, 16, b'\0' + b'\xff' * 8)]:
            for interlace in [0, 1]:
                self.assertTrue(ott.screenshot_png(png(1, 1, raw, depth, color, interlace), 1, 1))

    def test_output_existing_files_symlinks_directories_and_traversal_fail_before_network(self):
        existing = self.directory / 'existing.png'; existing.write_text('keep')
        paths = [existing, self.directory, self.directory / '..' / 'outside.png', self.directory / 'missing' / 'shot.png',
                 str(self.directory / 'newline') + '\n.png']
        if os.name != 'nt':
            link = self.directory / 'link.png'; link.symlink_to(existing)
            dangling = self.directory / 'dangling.png'; dangling.symlink_to(self.directory / 'absent')
            parent = self.directory / 'linked'; parent.symlink_to(self.directory, target_is_directory=True)
            paths += [link, dangling, parent / 'new.png']
        for path in paths:
            code, output, _, client = self.run_cli(['shot', '-o', str(path)], [])
            self.assertEqual((code, output, client.call.call_count), (1, '', 0))
        self.assertEqual(existing.read_text(), 'keep')

    def test_raced_destination_is_not_overwritten_and_no_temporary_file_remains(self):
        destination = self.directory / 'race.png'
        client = mock.Mock(timeout=45)
        def respond(_, action, __):
            if action == 'capabilities':
                return caps()
            destination.write_text('created by another process')
            return receipt()
        client.call.side_effect = respond
        code, output, errors, client = self.run_cli(['shot', '-o', str(destination)], client=client)
        self.assertEqual((code, output, client.call.call_count), (1, '', 2))
        self.assertIn('already exists', errors)
        self.assertEqual(destination.read_text(), 'created by another process')
        self.assertEqual(list(self.directory.glob('.ott-shot-*')), [])

    def test_capabilities_additive_validation_and_bare_player_permission_guidance(self):
        old = caps(); old.pop('screenshot')
        self.assertEqual(ott.capabilities_metadata(old), old)
        for state, source in [('ready', 'player-view'), ('ready', 'browser-tab'), ('permission_required', None), ('unsupported', None)]:
            value = caps(state, source)
            self.assertEqual(ott.capabilities_metadata(value), value)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ott.print_player_status({'capabilities': value}, 'tv')
            self.assertIn({'ready': 'ott tv screenshot / shot', 'permission_required': 'Select screenshot source in browser',
                           'unsupported': 'not supported'}[state], output.getvalue())
        for value in [None, {}, {'state': 'ready', 'source': None}, {'state': 'ready', 'source': 'evil'},
                      {'state': 'ready', 'source': 'window', 'private': 'secret'}, {'state': 'unknown', 'source': None}]:
            controls = caps(); controls['screenshot'] = value
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(controls)


if __name__ == '__main__':
    unittest.main()
