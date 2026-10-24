"""Status discovers current controls without causing player actions or hiding status."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_status', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class StatusCliTest(unittest.TestCase):
    def capabilities(self):
        return {'version': 1, 'player': {'version': '1.1.52-beta.48', 'platform': 'browser', 'runtime': 'page-one'},
                'lifecycle': ['reload_player', 'standby', 'wake'], 'input': ['up', 'ok'], 'playback': []}

    def run_cli(self, words, status, capabilities, machine=False, alias='tv'):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = [status, capabilities]
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(([machine if isinstance(machine, str) else '--json'] if machine else []) + [alias] + words)
        expected = [mock.call('dev_tv', 'status', {})]
        if not isinstance(status, ott.Error):
            expected.append(mock.call('dev_tv', 'capabilities', {}))
        self.assertEqual(client.call.call_args_list, expected)
        return code, output.getvalue(), errors.getvalue(), client

    def test_default_and_explicit_status_include_only_advertised_commands(self):
        for words in ([], ['status'], ['ST']):
            caps = self.capabilities()
            status = {'ready': True, 'volume': 100, 'player': caps['player']}
            code, out, err, client = self.run_cli(words, status, caps)
            self.assertEqual((code, err), (0, ''))
            self.assertTrue(out.startswith('Available controls now:\n'))
            self.assertIn('Not reported (catalog ready)', out)
            self.assertNotIn('"ready": true', out)
            self.assertLess(out.index('Request IDs:'), out.index('Player summary:'))
            self.assertIn('ott tv restart  — reload the player', out)
            self.assertIn('ott tv standby', out)
            self.assertIn('alias: stop', out)
            self.assertIn('ott tv go', out)
            self.assertIn('aliases: start, wake', out)
            self.assertIn('alias: restart player', out)
            self.assertIn('ott tv key KEY  — up, ok', out)
            for unsupported in ('restart stream', 'restart app', 'reboot', 'exit', 'seek', '+N', '-N'):
                self.assertNotIn(unsupported, out)
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'status', {}),
                                                         mock.call('dev_tv', 'capabilities', {})])
            self.assertEqual(client.timeout, 45)
            self.assertNotIn('capabilities', status)

    def test_json_retains_status_and_projects_validated_capabilities(self):
        caps = self.capabilities()
        status = {'ready': True, 'volume': 55, 'player': dict(caps['player'])}
        for words in ([], ['status'], ['st']):
            for flag in ('-j', '--json'):
                code, out, err, _ = self.run_cli(words, status, dict(caps, private='do-not-print'), flag)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(out, json.dumps(dict(status, capabilities=caps), ensure_ascii=False, indent=2) + '\n')
                self.assertNotIn('do-not-print', out)

    def test_standby_and_go_help_follow_independent_wire_capabilities(self):
        for operations in ([], ['standby'], ['wake'], ['standby', 'wake']):
            with self.subTest(operations=operations):
                caps = self.capabilities()
                caps['lifecycle'] = operations
                code, out, err, _ = self.run_cli([], {'ready': True}, caps)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual('ott tv standby  —' in out, 'standby' in operations)
                self.assertEqual('alias: stop' in out, 'standby' in operations)
                self.assertEqual('ott tv go  —' in out, 'wake' in operations)
                self.assertEqual('aliases: start, wake' in out, 'wake' in operations)

    def test_update_controls_are_discovered_only_when_the_plugin_is_advertised(self):
        for supported in (False, True):
            with self.subTest(supported=supported):
                caps = self.capabilities()
                caps.update(lifecycle=[], input=[], playback=[])
                caps["app_update"] = ({"version": 1, "operations": ["status", "prepare", "install"]}
                                      if supported else None)
                code, out, err, _ = self.run_cli([], {"ready": True}, caps)
                self.assertEqual((code, err), (0, ""))
                for example in ("update status", "update --variant api22 --prepare-only",
                                "update --variant full --prepare-only",
                                "update prepare HTTPS_APK_URL SHA256", "update install SHA256"):
                    self.assertEqual("ott tv " + example in out, supported)
                self.assertEqual("No controls are currently advertised" in out, not supported)
                if supported:
                    self.assertIn("Android confirmation", out)
                    self.assertIn("does not confirm installation", out)
                    self.assertNotIn("api22|full", out)

    def test_standby_snapshot_is_retained_without_comparing_caps_activity(self):
        for state, pending in [('running', 0), ('suspending', 3), ('suspending', None),
                               ('suspended', 0), ('unsupported', None), ('error', 2147483647)]:
            for machine in (False, True):
                caps = self.capabilities()
                status = {'ready': True, 'player': dict(caps['player'], standby=state != 'running',
                                                       background={'state': state, 'pending': pending})}
                # A later capabilities snapshot may observe progress; identity stays the same.
                caps['player'].update(standby=False, background={'state': 'running', 'pending': 0})
                code, out, err, _ = self.run_cli([], status, caps, machine)
                self.assertEqual((code, err), (0, ''))
                self.assertNotIn('player changed', out)
                if machine:
                    self.assertEqual(json.loads(out)['player'], status['player'])
                    self.assertNotIn('background', json.loads(out)['capabilities']['player'])
                else:
                    self.assertIn('Background:   ' + state + ' (' +
                                  (str(pending) if pending is not None else 'unknown') + ' pending)', out)

    def test_older_status_with_only_standby_never_claims_background_suspended(self):
        caps = self.capabilities()
        status = {'player': dict(caps['player'], standby=True)}
        code, out, err, _ = self.run_cli([], status, caps)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Background:   Not reported (unknown pending)', out)
        self.assertNotIn('suspended', out)

    def test_malformed_activity_snapshot_rejected_before_capability_request(self):
        invalid = [{'standby': value} for value in (None, 1, 'yes', [])]
        invalid.extend({'background': value} for value in
                       (None, {}, [], {'state': 'suspended'}, {'state': 'other', 'pending': 0},
                        {'state': 'suspended', 'pending': 0, 'url': 'private'}))
        invalid.extend({'background': {'state': 'suspended', 'pending': value}}
                       for value in (True, -1, 1.5, '0', 2147483648))
        for player in invalid:
            client = mock.Mock(timeout=45)
            with self.assertRaises(ott.Error):
                ott.status_capabilities(client, 'dev_tv', {'player': player}, float('inf'))
            client.call.assert_not_called()


    def test_provider_commands_include_m3u_and_contextual_profiles_without_extra_requests(self):
        for provider in ('m3u', 'vportal', 'stalker', 'plex'):
            for words in ([], ['status'], ['st']):
                with self.subTest(provider=provider, words=words):
                    caps = self.capabilities()
                    caps.update(lifecycle=[], input=[], playback=[])
                    status = {'ready': False, 'channels': 0, 'provider': provider}
                    code, out, err, _ = self.run_cli(words, status, caps, alias='Living room')
                    self.assertEqual((code, err), (0, ''))
                    self.assertIn("ott 'Living room' providers", out)
                    self.assertIn("ott 'Living room' provider m3u", out)
                    self.assertIn('alias: prov m3u', out)
                    self.assertIn("ott 'Living room' provider ID_OR_NAME", out)
                    self.assertIn('player settings and platform policy', out)
                    for command in ('profiles', 'profile N'):
                        self.assertEqual("ott 'Living room' " + command in out, provider in ('m3u', 'vportal'))
                    self.assertEqual("Example: ott 'Living room' profile 1" in out, provider in ('m3u', 'vportal'))
                    self.assertEqual('No channels loaded:' in out, provider == 'm3u')
                    if provider in ('m3u', 'vportal'):
                        self.assertIn('* marks the active profile', out)
                    self.assertNotIn('No controls are currently advertised', out)

    def test_missing_or_invalid_provider_does_not_infer_provider_commands(self):
        for provider in (None, '', '  ', [], {}):
            code, out, err, _ = self.run_cli([], {'provider': provider}, self.capabilities())
            self.assertEqual((code, err), (0, ''))
            self.assertNotIn('ott tv provider', out)

    def test_kiosk_keeps_provider_listing_but_explains_blocked_switching(self):
        for enabled in (True, False):
            for provider in ('m3u', 'vportal', 'stalker'):
                status = {'provider': provider, 'kiosk': {'enabled': enabled}}
                code, out, err, _ = self.run_cli([], status, self.capabilities(), alias='Living room')
                self.assertEqual((code, err), (0, ''))
                self.assertIn("ott 'Living room' providers", out)
                self.assertEqual("ott 'Living room' profiles" in out, provider in ('m3u', 'vportal'))
                self.assertEqual("ott 'Living room' provider m3u" in out, not enabled)
                self.assertEqual("ott 'Living room' profile N" in out, not enabled and provider in ('m3u', 'vportal'))
                self.assertEqual("Example: ott 'Living room' profile 1" in out, not enabled and provider in ('m3u', 'vportal'))
                self.assertEqual("run ott 'Living room' kiosk off first" in out, enabled)

    def test_provider_status_json_remains_machine_readable(self):
        caps = self.capabilities()
        status = {'ready': True, 'channels': 0, 'provider': 'm3u'}
        code, out, err, _ = self.run_cli([], status, caps, machine=True)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), dict(status, capabilities=caps))

    def test_default_status_lists_only_negotiated_workbench_sections(self):
        for sections in (['doctor'], ['snapshot'], ['operation'], ['doctor', 'snapshot', 'operation']):
            caps = self.capabilities()
            caps['inspect'] = {'version': 1, 'sections': sections}
            code, out, err, _ = self.run_cli([], {'ready': True}, caps, alias='Living room')
            self.assertEqual((code, err), (0, ''))
            for section, command in [('doctor', 'doctor'), ('snapshot', 'inspect --view ui,media'),
                                     ('snapshot', 'bundle --out DIR'), ('snapshot', 'test run health --report DIR'),
                                     ('snapshot', 'test run media-progress --report DIR'), ('operation', 'operation REQUEST_ID')]:
                self.assertEqual("ott 'Living room' " + command in out, section in sections)
            self.assertIn("ott --receipt 'Living room' COMMAND", out)
            self.assertIn("Local diagnostic help: ott 'Living room' test list", out)
            self.assertLess(out.index('Available diagnostics now:'), out.index('Player summary:'))
            self.assertLess(out.index('Request IDs:'), out.index('Player summary:'))
        code, out, err, _ = self.run_cli([], {'ready': True}, self.capabilities())
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn('Available diagnostics now', out)
        self.assertNotIn('ott tv doctor', out)
        self.assertNotIn('ott tv test run', out)
        self.assertIn('Local diagnostic help: ott tv test list', out)

    def test_optional_failure_keeps_status_and_never_echoes_raw_error(self):
        failures = [ott.PlayerUnsupported('private-token'), ott.PlayerRejected('private-token', {}),
                    ott.Error('private-token'), ott.TransportError('private-token'), ott.HTTPError(401),
                    {'version': 1, 'player': {'secret': 'private-token'}}]
        for failure in failures:
            for machine in (False, True):
                with self.subTest(failure=type(failure).__name__, machine=machine):
                    code, out, err, client = self.run_cli([], {'ready': True}, failure, machine)
                    self.assertEqual((code, err, len(client.call.call_args_list)), (0, '', 2))
                    self.assertNotIn('private-token', out)
                    self.assertNotIn('ott tv restart', out)
                    if machine:
                        self.assertIn('"ready": true', out)
                        value = json.loads(out)
                        self.assertIsNone(value['capabilities'])
                        self.assertTrue(value['capabilities_error'])
                    else:
                        self.assertIn('Not reported (catalog ready)', out)
                        self.assertNotIn('"ready": true', out)
                        self.assertLess(out.index('Local diagnostic help:'), out.index('Player summary:'))
                    self.assertEqual(client.timeout, 45)

    def test_standby_summary_is_last_and_does_not_confuse_catalog_with_playback(self):
        caps = self.capabilities()
        status = {'ready': False, 'channels': 0, 'provider': 'm3u', 'volume': 100,
                  'player': dict(caps['player'], standby=True,
                                 background={'state': 'suspended', 'pending': 0}),
                  'kiosk': {'enabled': False}}
        code, out, err, _ = self.run_cli([], status, caps, alias='d1')
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.endswith(
            '\nPlayer summary:\n'
            '  Player:       d1\n'
            '  Status:       Standby\n'
            '  Now playing:  Nothing (standby)\n'
            '  Provider:     M3U\n'
            '  Volume:       100%\n'
            '  Channels:     0 (catalog not ready)\n'
            '  Platform:     browser\n'
            '  Version:      1.1.52-beta.48\n'
            '  Background:   suspended (0 pending)\n'
            '  Kiosk:        Off\n'))
        self.assertLess(out.index('Request IDs:'), out.index('Player summary:'))

    def test_awake_and_local_video_catalog_never_claim_confirmed_playback_or_disconnection(self):
        for standby in (False, None):
            caps = self.capabilities()
            player = dict(caps['player'])
            if standby is not None:
                player['standby'] = standby
            status = {'player': player, 'ready': True, 'channels': 7, 'provider': 'offline', 'volume': 37.5}
            code, out, err, _ = self.run_cli([], status, caps)
            self.assertEqual((code, err), (0, ''))
            self.assertIn('Status:       ' + ('Awake' if standby is False else 'Connected (standby not reported)'), out)
            self.assertIn('Now playing:  Not reported', out)
            self.assertIn('Provider:     Local videos (offline)', out)
            self.assertIn('Channels:     7 (catalog ready)', out)
            self.assertIn('Volume:       37.5%', out)
            self.assertNotIn('Status:       Offline', out)

    def test_invalid_or_private_metadata_is_not_dumped_or_inferred(self):
        for invalid in (None, True, 'secret-value', {}, [], -1, 10 ** 400, float('nan'), float('inf')):
            with self.subTest(invalid=type(invalid).__name__):
                status = {'ready': 'secret-value', 'channels': invalid, 'volume': invalid,
                          'provider': 'https://example.invalid/?token=secret-token',
                          'uuid': 'private-device-uuid', 'diagnostics': {'password': 'secret-value'},
                          'title': 'unconfirmed-title'}
                code, out, err, _ = self.run_cli([], status, self.capabilities())
                self.assertEqual((code, err), (0, ''))
                self.assertIn('Volume:       Not reported', out)
                self.assertIn('Channels:     Not reported (catalog readiness not reported)', out)
                self.assertIn('Provider:     Not reported', out)
                for private in ('secret-value', 'secret-token', 'private-device-uuid', 'unconfirmed-title', 'example.invalid'):
                    self.assertNotIn(private, out)
        code, out, err, _ = self.run_cli([], {'provider': 'm3u', 'channels': False}, self.capabilities())
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn('No channels loaded:', out)

    def test_kiosk_target_is_bounded_plain_text_and_never_current_playback(self):
        for source in ('channel', 'media'):
            for enabled in (True, False):
                status = {'kiosk': {'enabled': enabled, 'strict': True,
                                    source: {'name' if source == 'channel' else 'title': 'РЕН\u2028ТВ\u202e\x00 HD'}}}
                code, out, err, _ = self.run_cli([], status, self.capabilities())
                self.assertEqual((code, err), (0, ''))
                self.assertIn('Now playing:  Not reported', out)
                self.assertEqual('Kiosk target: РЕН ТВ HD' in out, enabled)
                self.assertEqual('Kiosk:        On (strict)' in out, enabled)
                self.assertNotIn('\u2028', out)
                self.assertNotIn('\u202e', out)
                self.assertNotIn('\x00', out)
        for target in ({'token': 'secret-value'}, 'x' * 513, 'https://host.invalid/?token=secret-value'):
            code, out, err, _ = self.run_cli([], {'kiosk': {'enabled': True, 'channel': {'name': target}}},
                                            self.capabilities())
            self.assertEqual((code, err), (0, ''))
            self.assertIn('Kiosk target: Not reported', out)
            self.assertNotIn('secret-value', out)
            self.assertNotIn('x' * 513, out)

    def test_changed_runtime_or_invalid_identity_does_not_show_stale_controls(self):
        caps = self.capabilities()
        for identity in (dict(caps['player'], runtime='another-page'), {}, 'invalid'):
            code, out, err, _ = self.run_cli([], {'ready': True, 'player': identity}, caps, True)
            self.assertEqual((code, err), (0, ''))
            value = json.loads(out)
            self.assertIsNone(value['capabilities'])
            self.assertIn('player changed', value['capabilities_error'])

    def test_no_controls_is_explicit_and_parameterized_commands_are_usable(self):
        caps = self.capabilities()
        caps.update(lifecycle=[], input=[], playback=[])
        code, out, _, _ = self.run_cli([], {'ready': True}, caps)
        self.assertEqual(code, 0)
        self.assertIn('No controls are currently advertised', out)
        self.assertNotIn('ott tv stop', out)
        self.assertNotIn('ott tv start', out)
        self.assertNotIn('ott tv standby', out)
        self.assertNotIn('ott tv go', out)
        caps.update(lifecycle=['restart_stream', 'restart_app', 'exit_app', 'reboot_device'],
                    playback=['pause', 'resume', 'seek', 'previous_channel', 'next_channel', 'step_channel'])
        code, out, _, _ = self.run_cli([], {'ready': True}, caps, alias='Living room')
        for command in ('restart stream', 'restart app', 'exit', 'reboot', 'pause', 'resume', 'seek SECONDS', 'prev', 'next', '+N', '-N'):
            self.assertIn("ott 'Living room' " + command, out)
        self.assertNotIn('previous_channel', out)
        self.assertNotIn('next_channel', out)
        self.assertNotIn('step_channel', out)
        self.assertNotIn("ott 'Living room' restart  — reload", out)
        self.assertIn('restart only the current stream (alias: restart s)', out)

    def test_status_failure_never_submits_capabilities_request(self):
        code, _, _, client = self.run_cli([], ott.Error('offline'), self.capabilities())
        self.assertEqual(code, 1)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'status', {})])

    def test_secondary_call_uses_only_remaining_budget_and_restores_timeout(self):
        client = mock.Mock(timeout=45)
        budgets = []
        def reply(*_):
            budgets.append(client.timeout)
            return self.capabilities()
        client.call.side_effect = reply
        with mock.patch.object(ott.time, 'monotonic', side_effect=[12, 13]):
            result = ott.status_capabilities(client, 'dev_tv', {'ready': True}, 15)
        self.assertEqual(budgets, [3])
        self.assertEqual(client.timeout, 45)
        self.assertIsNotNone(result['capabilities'])
        with mock.patch.object(ott.time, 'monotonic', return_value=15):
            result = ott.status_capabilities(client, 'dev_tv', {'ready': True}, 15)
        self.assertEqual(client.call.call_count, 1)
        self.assertIsNone(result['capabilities'])
        with mock.patch.object(ott.time, 'monotonic', side_effect=[14, 16]):
            result = ott.status_capabilities(client, 'dev_tv', {'ready': True}, 15)
        self.assertIsNone(result['capabilities'])
        self.assertIn('--timeout', result['capabilities_error'])


if __name__ == '__main__':
    unittest.main()
