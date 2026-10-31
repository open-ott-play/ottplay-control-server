"""Provider diagnostics collect bounded evidence without source probes."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from test_bundle_debug_negotiation import NegotiatingClient
from test_workbench_cli import Clock, RUNTIME, load, ott, wb

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'contracts/provider-diagnostics-v1.json').read_text())
PRIVATE_MARKER = 'provider-private-value-never-export'
verify = load('provider_cli_report_verifier', 'report_verify.py')

class ProviderClient(NegotiatingClient):
    def __init__(self, clock):
        super().__init__(clock, 3)
        self.capabilities['provider_diagnostics'] = {'version': 1}
        self.provider = copy.deepcopy(FIXTURE['snapshot'])
        self.status_failure = None
        self.first_runtime = None

    def call_receipt(self, target, action, params):
        if action == 'status':
            self.calls.append((target, action, copy.deepcopy(params), self.timeout))
            if self.status_failure:
                self.clock.sleep(self.timeout)
                raise self.status_failure
            return {'request_id': format(len(self.calls), '032x'), 'status': 'ok',
                    'data': {'url': PRIVATE_MARKER, 'title': PRIVATE_MARKER,
                             'diagnostics': {'provider': copy.deepcopy(self.provider), 'config': PRIVATE_MARKER}}}
        result = super().call_receipt(target, action, params)
        if action == 'capabilities' and len(self.calls) == 1 and self.first_runtime:
            result['data']['player']['runtime'] = self.first_runtime
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

if __name__ == '__main__': unittest.main()
