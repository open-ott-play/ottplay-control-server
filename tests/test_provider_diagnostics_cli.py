"""Provider diagnostics collect bounded evidence without source probes."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from test_bundle_debug_negotiation import NegotiatingClient, versioned_debug
from test_workbench_cli import Clock, RUNTIME, load, ott, wb

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'contracts/provider-diagnostics-v1.json').read_text())
PRIVATE_MARKER = 'provider-private-value-never-export'
MISSING = object()
verify = load('provider_cli_report_verifier', 'report_verify.py')

class ProviderClient(NegotiatingClient):
    def __init__(self, clock):
        super().__init__(clock, 3)
        self.capabilities['provider_diagnostics'] = {'version': 1}
        self.provider = copy.deepcopy(FIXTURE['snapshot'])
        self.status_failure = None
        self.first_runtime = None
        self.debug_elapsed = 0
        self.companion_sibling = MISSING

    def call_receipt(self, target, action, params):
        if action == 'status':
            self.calls.append((target, action, copy.deepcopy(params), self.timeout))
            if self.status_failure:
                self.clock.sleep(self.timeout)
                raise self.status_failure
            diagnostics = {'provider': copy.deepcopy(self.provider), 'config': PRIVATE_MARKER}
            if self.companion_sibling is not MISSING:
                diagnostics['companion'] = copy.deepcopy(self.companion_sibling)
            return {'request_id': format(len(self.calls), '032x'), 'status': 'ok',
                    'data': {'url': PRIVATE_MARKER, 'title': PRIVATE_MARKER, 'diagnostics': diagnostics}}
        result = super().call_receipt(target, action, params)
        if action == 'capabilities' and len(self.calls) == 1 and self.first_runtime:
            result['data']['player']['runtime'] = self.first_runtime
        if action == 'inspect' and params['section'] == 'debug':
            self.clock.sleep(self.debug_elapsed)
        return result

class ProviderDiagnosticsCliTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = ProviderClient(self.clock)

    def execute(self, words, json_output=True):
        output = io.StringIO()
        with mock.patch.object(wb, 'time', self.clock), contextlib.redirect_stdout(output):
            code = wb.run(ott, self.client, 'web', words, 20, json_output)
        text = output.getvalue()
        self.assertNotIn(PRIVATE_MARKER, text)
        self.assertEqual(self.client.timeout, 20)
        self.assertTrue(all(call[1] in ('capabilities', 'status', 'inspect', 'maintenance') for call in self.client.calls))
        return code, json.loads(text) if json_output else text

    def use_companion_sibling(self, session=MISSING):
        self.client.capabilities['companion_diagnostics'] = copy.deepcopy(FIXTURE['companion_capability'])
        self.client.companion_sibling = copy.deepcopy(FIXTURE['companion_snapshot'])
        if session is not MISSING:
            self.client.companion_sibling['session'] = copy.deepcopy(session)

    def test_doctor_debug_and_alias_collect_three_samples(self):
        for command in ('doctor', 'debug', 'dbg'):
            self.client.calls.clear()
            code, report = self.execute([command, '--provider', '--lane', 'web'])
            self.assertEqual((code, report['evaluator'], report['verdict']), (0, 'workbench-v5', 'observed'))
            self.assertEqual(report['provider_observations'][0]['data'], FIXTURE['snapshot'])
            self.assertEqual(report['observations'][0]['data']['media']['phase'], 'playing')
            self.assertEqual(report['debug_observations'][0]['data']['version'], 3)
            self.assertEqual([call[1] for call in self.client.calls], ['capabilities', 'status', 'capabilities', 'inspect', 'capabilities', 'inspect'])
            self.assertEqual(next(call[2] for call in self.client.calls if call[1] == 'status'), {})

    def test_plain_http_failure_differs_from_successful_empty(self):
        code, text = self.execute(['doctor', '--provider', '--lane', 'web'], False)
        self.assertEqual(code, 0)
        self.assertIn('HTTP 400', text)
        self.assertIn('not a confirmed successful empty lookup', text)
        self.assertIn('Runtime correlation: matched', text)
        self.assertNotIn('"observations":', text)
        self.client.provider['lookup'].update(state='empty', failedAddons=0)
        self.assertIn('not a confirmed successful empty lookup', self.execute(['doctor', '--provider'], False)[1])
        self.client.provider['requests'][0].update(outcome='ok', httpStatus=200)
        text = self.execute(['doctor', '--provider'], False)[1]
        self.assertIn('no playable sources; no request error was reported', text)
        self.assertNotIn('source requests failed', text)

    def test_missing_capability_does_not_probe_and_unavailable_is_unknown(self):
        self.client.capabilities.pop('provider_diagnostics')
        code, report = self.execute(['doctor', '--provider', '--lane', 'web'])
        self.assertEqual((code, report['provider_observations'][0]['reason']), (3, 'unsupported'))
        self.assertFalse(any(call[1] == 'status' for call in self.client.calls))
        self.client.capabilities['provider_diagnostics'] = {'version': 1}
        self.client.provider = copy.deepcopy(FIXTURE['unavailable'])
        self.assertEqual(self.execute(['debug', '--provider'])[0], 3)

    def test_runtime_mismatch_and_cross_sample_mixing_are_distinct(self):
        self.client.provider['runtime'] = 'other-runtime'
        code, report = self.execute(['doctor', '--provider', '--lane', 'web'])
        self.assertEqual((code, report['provider_observations'][0]['reason']), (3, 'invalid_response'))
        self.client.calls.clear(); self.client.first_runtime = 'other-runtime'
        code, text = self.execute(['doctor', '--provider', '--lane', 'web'], False)
        self.assertEqual(code, 0)
        self.assertIn('Runtime correlation: mixed', text)

    def test_age_is_visible_without_claiming_atomic_freshness(self):
        self.client.provider['lookup']['ageMs'] = 61000
        text = self.execute(['doctor', '--provider'], False)[1]
        self.assertIn('age 61000 ms', text)
        self.assertIn('Lookup age exceeds 60 seconds', text)
        self.assertIn('samples were collected separately', text)

    def test_timeout_does_not_starve_or_replay(self):
        self.client.status_failure = ott.RequestError('request_pending_timeout', 'poll', PRIVATE_MARKER, request_id='a' * 32)
        code, report = self.execute(['doctor', '--provider', '--lane', 'web'])
        self.assertEqual((code, report['provider_observations'][0]['reason']), (3, 'request_pending_timeout'))
        self.assertEqual(report['observations'][0]['status'], 'observed')
        self.assertEqual(report['debug_observations'][0]['status'], 'observed')
        self.assertEqual(len([call for call in self.client.calls if call[1] == 'status']), 1)
        self.assertEqual(self.client.calls[1][3], 8)
        self.assertLessEqual(self.clock.value, 1020)

    def test_private_unknown_fields_never_export(self):
        self.client.provider['lookup']['url'] = PRIVATE_MARKER
        code, report = self.execute(['doctor', '--provider'])
        self.assertEqual((code, report['provider_observations'][0]['reason']), (3, 'invalid_response'))
        self.assertNotIn('data', report['provider_observations'][0])

    def test_dto_exact_bounds_types_and_identity(self):
        changes = [lambda d: d.update(version=True), lambda d: d.update(runtime='elsewhere'),
                   lambda d: d.update(capturedAt=-1), lambda d: d.update(requestsDropped=2**53),
                   lambda d: d['catalog'].update(busy=1), lambda d: d['lookup'].update(addonCount=9),
                   lambda d: d['lookup'].update(state='secret'), lambda d: d['requests'][0].update(httpStatus=99),
                   lambda d: d['requests'][0].update(lookupId=99), lambda d: d['lookup'].update(addonCount=1),
                   lambda d: d['requests'].append(copy.deepcopy(d['requests'][0])),
                   lambda d: d.update(requests=d['requests'] * 17), lambda d: d.update(available=False),
                   lambda d: d.update(lookup=None), lambda d: d.update(extra='x' * 17000),
                   lambda d: d.update(lookup=None, requests=[], requestsDropped=1)]
        for index, change in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(wb.InvalidData):
                data = copy.deepcopy(FIXTURE['snapshot']); change(data); wb.provider_metadata(data, RUNTIME)
        data = copy.deepcopy(FIXTURE['snapshot']); data['catalog'] = None
        self.assertIsNone(wb.provider_metadata(data, RUNTIME)['catalog'])

    def test_bundle_only_negotiates_provider_when_advertised(self):
        with tempfile.TemporaryDirectory() as directory:
            code, report = self.execute(['bundle', '--lane', 'web', '--out', str(Path(directory).resolve() / 'new')])
            self.assertEqual((code, report['evaluator']), (0, 'workbench-v5'))
            self.client.calls.clear(); self.client.capabilities.pop('provider_diagnostics')
            code, report = self.execute(['bundle', '--lane', 'web', '--out', str(Path(directory).resolve() / 'old')])
            self.assertEqual((code, report['evaluator']), (0, 'workbench-v4'))
            self.assertNotIn('provider_observations', report)
            self.assertFalse(any(call[1] == 'status' for call in self.client.calls))

    def test_actual_writer_roundtrips_all_commands_without_network(self):
        for command in ('bundle', 'doctor', 'debug'):
            self.client.calls.clear()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / 'report'
                words = [command, '--lane', 'web', '--out', str(path)]
                if command != 'bundle': words.append('--provider')
                code, report = self.execute(words)
                self.assertEqual(code, 0)
                output = io.StringIO()
                with contextlib.redirect_stdout(output), mock.patch.object(ott, 'Client', side_effect=AssertionError('network forbidden')):
                    verified = verify.main(['verify', str(path), '--json'], ott, wb)
                self.assertEqual(verified, 0, output.getvalue())
                self.assertEqual(json.loads(output.getvalue())['provider_correlation'], 'matched')
                self.assertNotIn(PRIVATE_MARKER, (path / 'result.json').read_text())

    def test_invalid_flags_do_not_send_requests(self):
        for words in (['doctor', '--out', 'unused'], ['debug', '--out', 'unused'], ['doctor', '--provider', '--lane', 'native']):
            self.client.calls.clear()
            code, report = self.execute(words)
            self.assertEqual((code, report['reason'], self.client.calls), (1, 'invalid_arguments', []))

    def test_companion_progress_and_terminal_failure_are_visible(self):
        for name in ('companion', 'companion_error'):
            with self.subTest(name=name):
                self.client.provider['companion'] = copy.deepcopy(FIXTURE[name])
                code, report = self.execute(['doctor', '--provider', '--lane', 'web'])
                self.assertEqual(code, 0)
                self.assertEqual(report['provider_observations'][0]['data']['companion'], FIXTURE[name])
                code, text = self.execute(['doctor', '--provider', '--lane', 'web'], False)
                self.assertEqual(code, 0)
                self.assertIn('Companion (cached)', text)
                self.assertIn(FIXTURE[name]['state'], text)
                if name == 'companion':
                    self.assertIn('lease active', text)
                    self.assertIn('1048576', text)
                    self.assertIn('8388608', text)
                else:
                    self.assertIn('lease inactive', text)
                    self.assertIn('cache_limit', text)
                    self.assertIn('unknown', text)

    def test_old_and_null_companion_data_remain_distinct_from_real_zero(self):
        old = wb.provider_metadata(copy.deepcopy(FIXTURE['snapshot']), RUNTIME)
        self.assertNotIn('companion', old)
        for base in ('snapshot', 'unavailable'):
            data = copy.deepcopy(FIXTURE[base])
            data['companion'] = None
            self.assertIsNone(wb.provider_metadata(data, RUNTIME)['companion'])
        data = copy.deepcopy(FIXTURE['snapshot'])
        data['companion'] = {**FIXTURE['companion'], 'downloaded': 0, 'total': 0}
        self.assertEqual(wb.provider_metadata(data, RUNTIME)['companion']['downloaded'], 0)
        data['companion'].update(downloaded=9007199254740991, total=None, state='unknown')
        self.assertEqual(wb.provider_metadata(data, RUNTIME)['companion'], data['companion'])

    def test_malformed_companion_fails_closed_without_private_output(self):
        changes = [lambda c: c.update(active=1), lambda c: c.update(state='playing'),
                   lambda c: c.update(error=PRIVATE_MARKER), lambda c: c.update(error=None),
                   lambda c: c.update(downloaded=True), lambda c: c.update(downloaded=-1),
                   lambda c: c.update(total=2**53), lambda c: c.update(total=1.5),
                   lambda c: c.update(url=PRIVATE_MARKER), lambda c: c.pop('active')]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                self.client.provider['companion'] = copy.deepcopy(FIXTURE['companion'])
                change(self.client.provider['companion'])
                code, report = self.execute(['doctor', '--provider', '--lane', 'web'])
                row = report['provider_observations'][0]
                self.assertEqual((code, row['reason']), (3, 'invalid_response'))
                self.assertNotIn('data', row)
        for bad in (False, [], '', 1):
            data = {**FIXTURE['snapshot'], 'companion': bad}
            with self.subTest(bad=bad), self.assertRaises(wb.InvalidData):
                wb.provider_metadata(data, RUNTIME)
        data = {**FIXTURE['unavailable'], 'companion': copy.deepcopy(FIXTURE['companion'])}
        with self.assertRaises(wb.InvalidData):
            wb.provider_metadata(data, RUNTIME)

    def test_private_reports_preserve_companion_without_probing_service(self):
        for command in ('bundle', 'doctor', 'debug'):
            self.client.calls.clear()
            self.client.provider['companion'] = copy.deepcopy(FIXTURE['companion_error'])
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / 'companion-case'
                words = [command, '--lane', 'web', '--out', str(path)]
                if command != 'bundle':
                    words.append('--provider')
                code, report = self.execute(words)
                self.assertEqual(code, 0)
                saved = json.loads((path / 'result.json').read_text())
                self.assertEqual(saved['provider_observations'][0]['data']['companion'], FIXTURE['companion_error'])
                self.assertEqual(report['provider_observations'], saved['provider_observations'])
                self.assertNotIn(PRIVATE_MARKER, (path / 'result.json').read_text())
                output = io.StringIO()
                with contextlib.redirect_stdout(output), \
                        mock.patch.object(ott, 'Client', side_effect=AssertionError('network forbidden')):
                    verified = verify.main(['verify', str(path), '--json'], ott, wb)
                self.assertEqual(verified, 0, output.getvalue())
                self.assertEqual(json.loads(output.getvalue())['provider_correlation'], 'matched')
                self.assertEqual(len([call for call in self.client.calls if call[1] == 'status']), 1)

    def test_engine_debug_retains_original_evidence_and_enriches_companion(self):
        self.client.provider['companion'] = copy.deepcopy(FIXTURE['companion'])
        code, report = self.execute(['debug', '--engine'])
        self.assertEqual((code, report['verdict'], report['evaluator']), (0, 'observed', 'workbench-v2'))
        self.assertEqual(report['observations'][0]['data'], versioned_debug(3))
        self.assertEqual(report['provider_observations'][0]['data']['companion'], FIXTURE['companion'])
        with self.assertRaises(verify.InvalidReport):
            verify.evaluate_report(report, ott, wb)
        self.assertEqual(self.client.calls[0][3], 20)
        self.assertEqual(self.client.calls[1][1:3], ('inspect', {'version': 3, 'runtime': RUNTIME, 'section': 'debug'}))
        self.assertEqual(self.client.calls[1][3], 20)
        self.assertEqual(len([call for call in self.client.calls if call[1] == 'inspect']), 1)
        self.assertEqual(len([call for call in self.client.calls if call[1] == 'status']), 1)
        self.client.calls.clear()
        self.assertIn('Companion (cached)', self.execute(['debug', '--engine'], False)[1])

    def test_engine_without_provider_capability_keeps_two_existing_reads(self):
        self.client.capabilities.pop('provider_diagnostics')
        code, report = self.execute(['debug', '--engine'])
        self.assertEqual((code, report['observations'][0]['data']), (0, versioned_debug(3)))
        self.assertNotIn('provider_observations', report)
        self.assertEqual([call[1] for call in self.client.calls], ['capabilities', 'inspect'])

    def test_provider_failure_cannot_downgrade_engine_debug(self):
        self.client.status_failure = ott.RequestError('request_pending_timeout', 'poll', PRIVATE_MARKER,
                                                     request_id='a' * 32)
        self.client.debug_elapsed = 19.75
        code, report = self.execute(['debug', '--engine'])
        self.assertEqual((code, report['verdict'], report['observations'][0]['data']),
                         (0, 'observed', versioned_debug(3)))
        self.assertEqual(report['provider_observations'][0]['status'], 'unknown')
        self.assertEqual(len([call for call in self.client.calls if call[1] == 'status']), 1)
        self.assertLessEqual(self.client.calls[-1][3], 0.25)
        self.assertLessEqual(self.clock.value, 1020)

    def test_engine_exhausting_deadline_does_not_restart_provider_budget(self):
        self.client.debug_elapsed = 20
        code, report = self.execute(['debug', '--engine'])
        self.assertEqual((code, report['verdict']), (3, 'unknown'))
        self.assertEqual([call[1] for call in self.client.calls], ['capabilities', 'inspect'])
        self.assertEqual(self.clock.value, 1020)

    def test_sibling_companion_is_normalized_without_changing_provider_wire_or_requests(self):
        self.use_companion_sibling()
        for words in (['doctor', '--provider', '--lane', 'web'], ['debug', '--engine']):
            self.client.calls.clear()
            code, report = self.execute(words)
            self.assertEqual((code, report['verdict']), (0, 'observed'))
            row = report['provider_observations'][0]
            self.assertEqual(row['data'], {**FIXTURE['snapshot'], 'companion': FIXTURE['companion']})
            self.assertEqual(row['capabilities']['companion_diagnostics'], {'version': 1})
            self.assertEqual(self.client.provider, FIXTURE['snapshot'])
            self.assertEqual([call[2] for call in self.client.calls if call[1] == 'status'], [{}])
            self.assertEqual(len(self.client.calls), 6 if words[0] == 'doctor' else 4)
        self.assertEqual(report['observations'][0]['data'], versioned_debug(3))
        self.assertEqual(len(self.client.calls), 4)
        self.assertIn('Companion (cached)', self.execute(['doctor', '--provider'], False)[1])

    def test_invalid_sibling_isolated_from_valid_provider_and_engine(self):
        self.use_companion_sibling()
        changes = [lambda d: d.update(version=True), lambda d: d.update(version=2),
                   lambda d: d.update(runtime='different-runtime'), lambda d: d.update(capturedAt=-1),
                   lambda d: d.update(capturedAt=True), lambda d: d.pop('capturedAt'),
                   lambda d: d.update(address=PRIVATE_MARKER),
                   lambda d: d['session'].update(downloaded=2**53),
                   lambda d: d['session'].update(total=True),
                   lambda d: d['session'].update(error=PRIVATE_MARKER),
                   lambda d: d['session'].update(source=PRIVATE_MARKER)]
        for index, change in enumerate(changes):
            self.client.companion_sibling = copy.deepcopy(FIXTURE['companion_snapshot'])
            change(self.client.companion_sibling)
            with self.subTest(index=index):
                code, report = self.execute(['debug', '--engine'])
                self.assertEqual((code, report['verdict']), (0, 'observed'))
                self.assertEqual(report['observations'][0]['data'], versioned_debug(3))
                self.assertEqual(report['provider_observations'][0]['data'],
                                 {**FIXTURE['snapshot'], 'companion': None})

    def test_unadvertised_or_unknown_sibling_capability_never_uses_legacy_fallback(self):
        self.use_companion_sibling()
        self.client.provider['companion'] = copy.deepcopy(FIXTURE['companion_error'])
        for marker in (MISSING, None, {}, {'version': True}, {'version': 2},
                       {'version': 1, 'url': PRIVATE_MARKER}):
            if marker is MISSING:
                self.client.capabilities.pop('companion_diagnostics', None)
            else:
                self.client.capabilities['companion_diagnostics'] = marker
            with self.subTest(marker=marker):
                code, report = self.execute(['doctor', '--provider'])
                row = report['provider_observations'][0]
                self.assertEqual((code, row['status']), (0, 'observed'))
                self.assertIsNone(row['data']['companion'])
                self.assertNotIn('companion_diagnostics', row['capabilities'])

    def test_sibling_presence_wins_and_missing_sibling_preserves_legacy_behavior(self):
        self.use_companion_sibling()
        self.client.provider['companion'] = copy.deepcopy(FIXTURE['companion_error'])
        self.assertEqual(self.execute(['doctor', '--provider'])[1]['provider_observations'][0]['data']['companion'],
                         FIXTURE['companion'])
        self.client.provider['companion'] = {'private': PRIVATE_MARKER}
        self.assertEqual(self.execute(['doctor', '--provider'])[1]['provider_observations'][0]['data']['companion'],
                         FIXTURE['companion'])
        self.client.provider['companion'] = copy.deepcopy(FIXTURE['companion_error'])
        for sibling in (None, {}, {**FIXTURE['companion_snapshot'], 'session': None}):
            self.client.companion_sibling = sibling
            code, report = self.execute(['doctor', '--provider'])
            self.assertEqual(code, 0)
            self.assertIsNone(report['provider_observations'][0]['data']['companion'])
        self.client.companion_sibling = MISSING
        self.assertEqual(self.execute(['doctor', '--provider'])[1]['provider_observations'][0]['data']['companion'],
                         FIXTURE['companion_error'])
        self.client.provider.pop('companion')
        self.assertEqual(self.execute(['doctor', '--provider'])[1]['provider_observations'][0]['data'],
                         FIXTURE['snapshot'])

    def test_sibling_does_not_expand_provider_scope_or_hide_invalid_provider(self):
        self.use_companion_sibling()
        self.client.provider = copy.deepcopy(FIXTURE['unavailable'])
        code, report = self.execute(['doctor', '--provider'])
        self.assertEqual(code, 3)
        self.assertEqual(report['provider_observations'][0]['data'], {**FIXTURE['unavailable'], 'companion': None})
        self.client.provider = {**FIXTURE['snapshot'], 'url': PRIVATE_MARKER}
        code, report = self.execute(['doctor', '--provider'])
        self.assertEqual((code, report['provider_observations'][0]['reason']), (3, 'invalid_response'))
        self.client.capabilities.pop('provider_diagnostics')
        self.client.calls.clear()
        code, report = self.execute(['debug', '--engine'])
        self.assertEqual((code, report['observations'][0]['data']), (0, versioned_debug(3)))
        self.assertNotIn('provider_observations', report)
        self.assertEqual([call[1] for call in self.client.calls], ['capabilities', 'inspect'])

    def test_sibling_bundle_and_provider_reports_verify_as_normalized_v5(self):
        self.use_companion_sibling(FIXTURE['companion_error'])
        for command in ('bundle', 'doctor', 'debug'):
            self.client.calls.clear()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / 'sibling-report'
                words = [command, '--lane', 'web', '--out', str(path)]
                if command != 'bundle':
                    words.append('--provider')
                code, report = self.execute(words)
                self.assertEqual((code, report['evaluator']), (0, 'workbench-v5'))
                saved_text = (path / 'result.json').read_text()
                self.assertNotIn(PRIVATE_MARKER, saved_text)
                saved = json.loads(saved_text)
                row = saved['provider_observations'][0]
                self.assertEqual(row['data']['companion'], FIXTURE['companion_error'])
                self.assertEqual(row['capabilities']['companion_diagnostics'], {'version': 1})
                self.assertNotIn('session', row['data']['companion'])
                output = io.StringIO()
                with contextlib.redirect_stdout(output), \
                        mock.patch.object(ott, 'Client', side_effect=AssertionError('network forbidden')):
                    verified = verify.main(['verify', str(path), '--json'], ott, wb)
                self.assertEqual(verified, 0, output.getvalue())
                self.assertEqual(json.loads(output.getvalue())['provider_correlation'], 'matched')
                self.assertEqual(len([call for call in self.client.calls if call[1] == 'status']), 1)

if __name__ == '__main__': unittest.main()
