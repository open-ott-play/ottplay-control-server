"""Optional video evidence is a safe display projection, never playback proof."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('ott_video_evidence', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
MISSING = object()


class VideoEvidenceCliTest(unittest.TestCase):
    def evidence(self):
        return {'version': 1, 'window_present_allowed': True, 'native': {
            'position_callbacks': 12, 'sink_report': {
                'callback_type': 49, 'callback_count': 3, 'firmware_major': 5, 'raw_value': '0'}}}

    def run_status(self, evidence=MISSING, *, details=True, machine=False):
        caps = {'version': 1, 'player': {'version': '1.2.3', 'platform': 'webos-native',
                                       'runtime': 'fixture-one', 'standby': False},
                'input': [], 'playback': [], 'lifecycle': [], 'playback_failure_versions': [2, 3]}
        status = {'player': dict(caps['player']), 'volume': 55, 'provider': 'vportal',
                  'playback_failure': None, 'playback': {
                      'state': 'playing', 'kind': 'vod', 'title': 'Example movie', 'provider': 'vportal',
                      'media_id': 'fixture:0123456789abcdef', 'channel_number': None,
                      'position': 12, 'duration': 120, 'volume': 55}}
        if evidence is not MISSING:
            status['video_evidence'] = evidence
        before = copy.deepcopy(status)
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = [caps, status] if details else [status, caps]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                mock.patch.object(ott, 'television_module'), mock.patch.object(ott, 'device_power_module'), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['--json'] if machine else []) + ['tv', 'status'] + (['--details'] if details else []))
        self.assertEqual((code, err.getvalue()), (0, ''))
        self.assertEqual(status, before)
        self.assertEqual(client.timeout, 45)
        expected = ([mock.call('dev_tv', 'capabilities', {}),
                     mock.call('dev_tv', 'status', {'playback_failure_version': 3})] if details else
                    [mock.call('dev_tv', 'status', {}), mock.call('dev_tv', 'capabilities', {})])
        self.assertEqual(client.call.call_args_list, expected)
        return out.getvalue(), before, caps

    def test_absent_evidence_adds_no_output_and_default_text_is_unchanged(self):
        absent, _, _ = self.run_status()
        self.assertNotIn('Video evidence:', absent)
        self.assertNotIn('Sink raw report:', absent)
        baseline, _, _ = self.run_status(details=False)
        for evidence in (self.evidence(), None, {'version': 99, 'secret': 'private text'}):
            output, _, _ = self.run_status(evidence, details=False)
            self.assertEqual(output, baseline)

    def test_raw_zero_reports_observations_without_health_or_frames_claims(self):
        output, _, _ = self.run_status(self.evidence())
        self.assertIn('Video evidence: window present permission=allowed; position callbacks=12', output)
        self.assertIn('Sink raw report: type=49; callbacks=3; firmware major=5; raw=0', output)
        lines = [line for line in output.splitlines() if 'Video evidence:' in line or 'Sink raw report:' in line]
        self.assertEqual(len(lines), 2)
        for word in ('frames', 'healthy', 'visible', 'power', 'success'):
            self.assertNotIn(word, '\n'.join(lines).lower())
        self.assertIn('Now playing:  Example movie (playing)', output)

    def test_native_and_sink_null_are_valid_and_window_false_is_explicit(self):
        evidence = self.evidence()
        evidence.update(window_present_allowed=False, native=None)
        output, _, _ = self.run_status(evidence)
        self.assertIn('window present permission=not allowed; native callbacks not reported', output)
        self.assertNotIn('invalid metadata', output)
        evidence['native'] = {'position_callbacks': 0, 'sink_report': None}
        output, _, _ = self.run_status(evidence)
        self.assertIn('position callbacks=0', output)
        self.assertIn('Sink raw report: not reported', output)
        self.assertNotIn('invalid metadata', output)

    def test_i64_limits_u32_limits_and_firmware_callback_selection(self):
        for raw in ('-9223372036854775808', '9223372036854775807'):
            for major, callback in ((0, 47), (4, 47), (5, 49), (4294967295, 49)):
                with self.subTest(raw=raw, major=major):
                    evidence = self.evidence()
                    evidence['native']['position_callbacks'] = 4294967295
                    evidence['native']['sink_report'].update(callback_type=callback, callback_count=4294967295,
                                                               firmware_major=major, raw_value=raw)
                    output, _, _ = self.run_status(evidence)
                    self.assertIn('position callbacks=4294967295', output)
                    self.assertIn(f'type={callback}; callbacks=4294967295; firmware major={major}; raw={raw}', output)

    def invalid_examples(self):
        yield 'root_null', None
        yield 'root_list', []
        yield 'root_string', 'https://private.invalid'
        yield 'unknown_version', dict(self.evidence(), version=2)
        yield 'bool_version', dict(self.evidence(), version=True)
        yield 'extra_root', dict(self.evidence(), private='https://private.invalid')
        yield 'window_int', dict(self.evidence(), window_present_allowed=1)
        for path in (('native',), ('native', 'position_callbacks'), ('native', 'sink_report'),
                     ('native', 'sink_report', 'callback_type'), ('native', 'sink_report', 'raw_value')):
            value = self.evidence(); node = value
            for key in path[:-1]:
                node = node[key]
            del node[path[-1]]
            yield 'missing_' + '_'.join(path), value
        for field, invalids in (
                ('position_callbacks', (True, -1, 4294967296, 1.0, '1')),
                ('sink_report', ([], 'https://private.invalid'))):
            for invalid in invalids:
                value = self.evidence(); value['native'][field] = invalid
                yield 'native_' + field, value
        value = self.evidence(); value['native']['extra'] = 'private'
        yield 'extra_native', value
        for field, invalids in (
                ('callback_type', (True, 47, 48, 49.0, '49')),
                ('callback_count', (True, 0, -1, 4294967296, 1.0)),
                ('firmware_major', (True, -1, 4294967296, 0, 5.0)),
                ('raw_value', (0, True, None, [], '+1', '-0', '00', '01', '-01', ' 0', '0 ', '0\n',
                               '\x1b[31mprivate', 'https://private.invalid', '\u0661', '1' * 1000,
                               '9223372036854775808', '-9223372036854775809'))):
            for invalid in invalids:
                value = self.evidence(); value['native']['sink_report'][field] = invalid
                yield 'sink_' + field, value
        value = self.evidence(); value['native']['sink_report']['extra'] = 'private'
        yield 'extra_sink', value

    def test_invalid_optional_evidence_preserves_playback_and_opaque_json(self):
        for name, invalid in self.invalid_examples():
            with self.subTest(name=name, value=repr(invalid)[:80]):
                output, _, _ = self.run_status(invalid)
                self.assertIn('Video evidence: unavailable (invalid metadata).', output)
                self.assertIn('Now playing:  Example movie (playing)', output)
                for forbidden in ('https://private.invalid', '\x1b[31mprivate', '1' * 1000):
                    self.assertNotIn(forbidden, output)
                raw, status, caps = self.run_status(invalid, machine=True)
                self.assertEqual(json.loads(raw), dict(status, capabilities=caps))

    def test_valid_evidence_passes_through_json_unchanged(self):
        for details in (False, True):
            raw, status, caps = self.run_status(self.evidence(), details=details, machine=True)
            self.assertEqual(json.loads(raw), dict(status, capabilities=caps))


if __name__ == '__main__':
    unittest.main()
