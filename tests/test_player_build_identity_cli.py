"""Optional build identity remains separate from the reported player version."""
import contextlib
import copy
import io
import json
import unittest
from unittest import mock

import test_native_playback_capture_cli as capture_test
import test_status_cli as status_test


FULL_SHA = '0123456789abcdef0123456789abcdef01234567'
VALID_BUILDS = ('abcd', '123abcd', '0123456789ab', FULL_SHA, 'unknown')
INVALID_BUILDS = (None, True, False, 1234567, 1.5, [], {}, '', 'abc', 'a' * 41,
                  'ABCDEF1', 'Unknown', 'unknown ', ' abcdef1', 'abcdef1\n',
                  'abcd\x00ef', 'abcd\t123', 'abcd\r123', 'abcd\x1b[31m',
                  'https://private.invalid/token', '/Volumes/private/Movies', 'сборка')
RELEASE_VERSION = '1.4.2-beta.7'


class PlayerBuildIdentityCLITest(unittest.TestCase):
    def capabilities(self, build=None, *, present=True):
        caps = status_test.StatusCliTest.capabilities(self)
        caps['player'].update(version=RELEASE_VERSION, platform='webos-native')
        if present:
            caps['player']['build_sha'] = build
        return caps

    def status(self, caps, *, machine=False):
        status = {'ready': True, 'volume': 55, 'player': copy.deepcopy(caps['player'])}
        return status_test.StatusCliTest.run_cli(self, ['status'], status, caps, machine, 'l_')

    def capture(self, player, *, machine=True):
        clock = capture_test.Clock()
        client = capture_test.CaptureClient(clock)
        client.capabilities['player'].update(player)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(capture_test.capture, 'time', clock), \
                mock.patch.object(capture_test.ott, 'native_playback_capture_module', return_value=capture_test.capture), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = capture_test.capture.run(capture_test.ott, client, 'web', ['start'], 20, machine)
        return code, json.loads(out.getvalue()) if machine else out.getvalue(), err.getvalue(), client

    def test_build_identity_is_optional_without_changing_legacy_player_fields(self):
        caps = self.capabilities(present=False)
        before = copy.deepcopy(caps)
        self.assertEqual(status_test.ott.capabilities_metadata(caps), before)
        code, text, err, _ = self.status(caps)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Version:      ' + RELEASE_VERSION + '\n', text)
        self.assertNotIn('(build', text)
        code, result, err, _ = self.capture(caps['player'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(result['player'], caps['player'])
        self.assertNotIn('build_sha', result['player'])

    def test_valid_build_identity_is_projected_without_rewriting_version_or_markers(self):
        for build in VALID_BUILDS:
            for marker in ({}, {'native_playback_diagnostics': {'version': 1}},
                           {'native_playback_capture': copy.deepcopy(capture_test.CAPABILITY)}):
                with self.subTest(build=build, marker=tuple(marker)):
                    caps = self.capabilities(build)
                    caps.update(marker)
                    before = copy.deepcopy(caps)
                    parsed = status_test.ott.capabilities_metadata(caps)
                    self.assertEqual(parsed, before)
                    self.assertEqual(parsed['player']['version'], RELEASE_VERSION)
                    parsed['player']['build_sha'] = 'ffff'
                    self.assertEqual(caps, before)

    def test_invalid_build_identity_is_rejected_with_a_generic_capability_error(self):
        for build in INVALID_BUILDS:
            with self.subTest(build=repr(build)):
                with self.assertRaisesRegex(status_test.ott.Error,
                                            '^The player returned invalid capabilities metadata$'):
                    status_test.ott.capabilities_metadata(self.capabilities(build))

    def test_status_text_abbreviates_only_the_hash_and_json_retains_full_identity(self):
        for build in VALID_BUILDS:
            with self.subTest(build=build):
                caps = self.capabilities(build)
                code, text, err, _ = self.status(caps)
                self.assertEqual((code, err), (0, ''))
                suffix = 'build SHA not reported' if build == 'unknown' else 'build ' + build[:12]
                self.assertIn('Version:      ' + RELEASE_VERSION + ' (' + suffix + ')\n', text)
                if len(build) > 12:
                    self.assertNotIn(build, text)
                code, raw, err, _ = self.status(caps, machine=True)
                self.assertEqual((code, err), (0, ''))
                result = json.loads(raw)
                self.assertEqual(result['player'], caps['player'])
                self.assertEqual(result['capabilities']['player'], caps['player'])

    def test_status_text_does_not_render_unvalidated_build_strings(self):
        for build in INVALID_BUILDS:
            with self.subTest(build=repr(build)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    status_test.ott.print_player_summary({'player': self.capabilities(build)['player']}, 'l_')
                text = out.getvalue()
                self.assertIn('Version:      ' + RELEASE_VERSION + '\n', text)
                self.assertNotIn('(build', text)

    def test_invalid_status_build_identity_never_reaches_text_or_json_output(self):
        for build in INVALID_BUILDS:
            for machine in (False, True):
                with self.subTest(build=repr(build), machine=machine):
                    client = mock.Mock(timeout=45)
                    client.device.return_value = 'dev_tv'
                    client.call.return_value = {'ready': True, 'player': self.capabilities(build)['player']}
                    out, err = io.StringIO(), io.StringIO()
                    with mock.patch.object(status_test.ott, 'Client', return_value=client), \
                            mock.patch.object(status_test.ott, 'read_json', return_value={}), \
                            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = status_test.ott.main((['--json'] if machine else []) + ['l_', 'status'])
                    self.assertEqual((code, out.getvalue()), (1, ''))
                    self.assertEqual(err.getvalue(), 'Error: The player returned invalid build SHA metadata\n')
                    self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'status', {})])

    def test_capture_reports_validated_player_identity_and_preserves_full_hash_in_json(self):
        for build in VALID_BUILDS:
            with self.subTest(build=build):
                player = self.capabilities(build)['player']
                code, result, err, client = self.capture(dict(player, private='private-profile-name'))
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(result['player'], player)
                self.assertEqual(result['runtime'], player['runtime'])
                self.assertNotIn('private-profile-name', json.dumps(result))
                self.assertEqual([row[1] for row in client.calls],
                                 ['capabilities', 'native_playback_diagnostics'])
                self.assertEqual(client.calls[-1][2]['operation'], 'start')
                self.assertNotIn('build_sha', client.calls[-1][2])

    def test_capture_text_displays_release_version_and_short_build_identity(self):
        for build in VALID_BUILDS:
            with self.subTest(build=build):
                code, text, err, _ = self.capture(self.capabilities(build)['player'], machine=False)
                self.assertEqual((code, err), (0, ''))
                suffix = 'build SHA not reported' if build == 'unknown' else 'build ' + build[:12]
                self.assertIn(RELEASE_VERSION + ' (' + suffix + ')', text)
                if len(build) > 12:
                    self.assertNotIn(build, text)

    def test_invalid_build_capabilities_stop_before_capture_without_private_output(self):
        for build in INVALID_BUILDS:
            with self.subTest(build=repr(build)):
                code, result, err, client = self.capture(self.capabilities(build)['player'])
                self.assertEqual((code, result['reason'], err), (3, 'invalid_response', ''))
                self.assertFalse(result['unknown_outcome'])
                self.assertNotIn('data', result)
                self.assertNotIn('player', result)
                self.assertEqual([row[1] for row in client.calls], ['capabilities'])
                for private in ('private.invalid', '/Volumes/private', '\x1b', '\x00'):
                    self.assertNotIn(private, json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
