"""Native capture commands preserve scope, privacy and uncertain outcomes."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from test_native_playback_cli import NativeClient, NATIVE_RUNTIME, PRIVATE_URL
from test_workbench_cli import Clock, SECRET, load, ott, wb


capture = load('native_capture_tests', 'native_playback_capture.py')
ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / 'contracts/native-playback-capture-v1.json'
FIXTURE = json.loads(FIXTURE_PATH.read_text())
CAPTURE_ID = 'cap_' + 'a' * 32
OTHER_ID = 'cap_' + 'b' * 32
CAPABILITY = {'version': 1, 'operations': ['start', 'status', 'read', 'stop'],
              'default_duration_s': 30, 'min_duration_s': 5, 'max_duration_s': 60,
              'sample_interval_ms': 1000, 'max_records': 64}
PRIVATE_VALUES = (SECRET, PRIVATE_URL, 'PrivateProfileHome', 'РЕН ТВ HD',
                  'Три кота', '/Volumes/private/Movies/secret.mp4')


def case(name):
    return copy.deepcopy(next(row for row in FIXTURE['cases'] if row['name'] == name))


class CaptureClient(NativeClient):
    def __init__(self, clock):
        super().__init__(clock)
        self.capabilities['native_playback_capture'] = copy.deepcopy(CAPABILITY)
        self.replies = {operation: case(name)['data'] for operation, name in (
            ('start', 'start'), ('status', 'active_discovery'), ('read', 'read_active'), ('stop', 'stop_active'))}
        self.capability_failure = None
        self.probe_capabilities = None
        self.capture_barrier = None
        self.capture_hook = None

    def call_receipt(self, target, action, params):
        if getattr(self, 'group_cancelled', None) is not None and self.group_cancelled.is_set():
            raise KeyboardInterrupt
        if action == 'capabilities':
            self.calls.append((target, action, copy.deepcopy(params), self.timeout))
            if self.capability_failure is not None:
                raise self.capability_failure
            data = self.probe_capabilities if params and self.probe_capabilities is not None else self.capabilities
        elif action == 'native_playback_diagnostics':
            self.calls.append((target, action, copy.deepcopy(params), self.timeout))
            if self.capture_hook is not None:
                self.capture_hook(self, params)
            if self.capture_barrier is not None:
                self.capture_barrier.wait(timeout=5)
            data = self.replies[params['operation']]
            if callable(data):
                data = data(params)
            if isinstance(data, BaseException):
                raise data
        else:
            return super().call_receipt(target, action, params)
        return {'request_id': format(len(self.calls), '032x'), 'status': 'ok', 'data': copy.deepcopy(data)}

    def for_group(self, cancelled):
        child = CaptureClient(Clock())
        child.group_cancelled = cancelled
        child.capabilities = copy.deepcopy(self.capabilities)
        child.replies = copy.deepcopy(self.replies)
        child.capture_barrier = self.capture_barrier
        child.capture_hook = self.capture_hook
        self.children.append(child)
        return child


class NativePlaybackCaptureCLITest(unittest.TestCase):
    def test_priming_extension_is_closed_bounded_and_legacy_compatible(self):
        legacy = case('read_active')['data']['records'][0]
        self.assertEqual(capture.record(legacy), legacy)
        fields = {
            'prime_pending': True, 'prime_kind': 'fresh',
            'prime_required_runway_ms': 2000,
            'prime_accepted_video_ms': 1000, 'prime_accepted_audio_ms': 1000,
            'prime_queued_video_ms': 2700, 'prime_queued_audio_ms': 2700,
            'prime_external_lease': True, 'prime_audio_expected': True,
        }
        contract = json.loads((ROOT / 'contracts/native-playback-priming-v1.json').read_text())
        self.assertEqual(contract['parent_sha256'], hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest())
        self.assertEqual(set(contract['fields']), set(fields))
        extended = dict(legacy, **fields)
        self.assertEqual(capture.record(extended), extended)
        for key in fields:
            partial = dict(extended)
            del partial[key]
            with self.subTest(missing=key), self.assertRaises(capture.InvalidData):
                capture.record(partial)
        for key, bad in [('prime_kind', PRIVATE_URL), ('prime_pending', 1),
                         ('prime_queued_video_ms', -1), ('prime_queued_audio_ms', 86400001),
                         ('prime_required_runway_ms', True), ('private_url', PRIVATE_URL)]:
            with self.subTest(field=key, bad=bad), self.assertRaises(capture.InvalidData):
                capture.record(dict(extended, **{key: bad}))

    def setUp(self):
        self.clock = Clock()
        self.client = CaptureClient(self.clock)

    def execute(self, words, *, json_output=True, timeout=20, client=None, route=False):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(capture, 'time', self.clock), \
                mock.patch.object(ott, 'native_playback_capture_module', return_value=capture), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            if route:
                code = wb.run(ott, client or self.client, 'web', ['debug', 'capture', *words], timeout, json_output)
            else:
                code = capture.run(ott, client or self.client, 'web', words, timeout, json_output)
        printed = output.getvalue() + errors.getvalue()
        if json_output and output.getvalue():
            printed += json.dumps(json.loads(output.getvalue()), ensure_ascii=False)
        for private in PRIVATE_VALUES:
            self.assertNotIn(private, printed)
        return code, json.loads(output.getvalue()) if json_output else output.getvalue(), errors.getvalue()

    def capture_calls(self):
        return [call for call in self.client.calls if call[1] == 'native_playback_diagnostics']

    def test_capture_capability_is_closed_typed_and_additive(self):
        self.assertEqual(FIXTURE['capability'], CAPABILITY)
        self.assertEqual(capture.CAPABILITY, CAPABILITY)
        self.assertEqual(capture.capability(CAPABILITY), CAPABILITY)
        for passive in (True, False):
            controls = copy.deepcopy(self.client.capabilities)
            if not passive:
                controls.pop('native_playback_diagnostics')
            parsed = ott.capabilities_metadata(controls)
            self.assertEqual(parsed['native_playback_capture'], CAPABILITY)
            self.assertEqual('native_playback_diagnostics' in parsed, passive)
        mutations = [None, {}, True, dict(CAPABILITY, version=True), dict(CAPABILITY, version=2),
                     dict(CAPABILITY, max_duration_s=120), dict(CAPABILITY, max_records=65),
                     dict(CAPABILITY, sample_interval_ms=1000.0), dict(CAPABILITY, url=PRIVATE_URL),
                     dict(CAPABILITY, operations=['start', 'status', 'read', 'read']),
                     dict(CAPABILITY, operations=['start', 'status', 'read', 'stop', 'eval'])]
        for marker in mutations:
            with self.subTest(marker=marker), self.assertRaises((capture.InvalidData, ValueError)):
                capture.capability(marker)
            with self.subTest(marker=marker), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(self.client.capabilities, native_playback_capture=marker))

    def test_invalid_arguments_never_send_any_rpc(self):
        invalid = [[], ['unknown'], ['start', '--duration', '4'], ['start', '--duration', '61'],
                   ['start', '--duration', '120'], ['start', '--duration', '5.0'],
                   ['start', '--duration', 'nan'], ['start', '--duration', 'inf'],
                   ['status', '--duration', '5'], ['read', CAPTURE_ID], ['stop', CAPTURE_ID],
                   ['start', '--out', '/unused'], ['start', '--lane', 'native'],
                   ['start', '--native-playback'], ['start', '--engine']]
        for words in invalid:
            with self.subTest(words=words):
                self.client.calls.clear()
                code, result, _ = self.execute(words)
                self.assertNotEqual(code, 0)
                self.assertEqual(result['reason'], 'invalid_arguments')
                self.assertEqual(self.client.calls, [])

    def test_shared_fixture_accepts_all_success_and_typed_error_responses(self):
        self.assertEqual(hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
                         '3f580425a16016b4b6201d996a644bc56705e4b7ea706bb6f68918969fa533fa')
        self.assertEqual((len(FIXTURE['cases']), len(FIXTURE['error_cases'])), (19, 28))
        for row in FIXTURE['cases'] + FIXTURE['error_cases']:
            with self.subTest(name=row['name']):
                self.assertEqual(capture.metadata(row['data'], row['request'], row['status']), row['data'])

    def test_start_uses_relay_probe_and_one_runtime_bound_mutation(self):
        for duration in (None, 5, 60):
            for passive in (True, False):
                with self.subTest(duration=duration, passive=passive):
                    self.client = CaptureClient(self.clock)
                    if not passive:
                        self.client.capabilities.pop('native_playback_diagnostics')
                    words = ['start'] + ([] if duration is None else ['--duration', str(duration)])
                    code, result, _ = self.execute(words)
                    self.assertEqual((code, result['verdict']), (0, 'observed'))
                    self.assertFalse(result['read_only'])
                    self.assertFalse(result['unknown_outcome'])
                    self.assertFalse(result['physical_display_verified'])
                    self.assertEqual([row[1:3] for row in self.client.calls], [
                        ('capabilities', {'native_playback_capture': True}),
                        ('native_playback_diagnostics', dict(case('start')['request'], duration_s=duration or 30))])
                    self.assertEqual(self.client.timeout, 20)

    def test_old_relay_or_missing_capability_never_falls_back_to_capture(self):
        for failure in (None, ott.HTTPError(400), ott.HTTPError(404)):
            with self.subTest(failure=failure):
                self.client = CaptureClient(self.clock)
                self.client.capabilities.pop('native_playback_capture')
                self.client.capability_failure = failure
                code, result, _ = self.execute(['start'])
                self.assertEqual(code, 3)
                self.assertFalse(result['unknown_outcome'])
                self.assertEqual([row[1:3] for row in self.client.calls],
                                 [('capabilities', {'native_playback_capture': True})])

    def test_read_and_stop_discover_then_pin_exact_id_without_restarting_capture(self):
        for operation in ('read', 'stop'):
            for state in FIXTURE['stop_states'] + ['capturing']:
                with self.subTest(operation=operation, state=state):
                    self.client = CaptureClient(self.clock)
                    self.client.replies['status']['state'] = state
                    name = operation + ('_active' if state == 'capturing' else '_' + state)
                    self.client.replies[operation] = case(name)['data']
                    code, result, _ = self.execute([operation])
                    self.assertEqual((code, result['data']), (0, case(name)['data']))
                    self.assertEqual(result['read_only'], operation == 'read')
                    self.assertEqual([row[2] for row in self.capture_calls()],
                                     [case('idle_discovery')['request'], case(name)['request']])

    def test_idle_discovery_never_sends_read_or_stop(self):
        for operation in ('status', 'read', 'stop'):
            with self.subTest(operation=operation):
                self.client = CaptureClient(self.clock)
                self.client.replies['status'] = case('idle_discovery')['data']
                code, result, _ = self.execute([operation])
                self.assertEqual((code, result['data']), (0, case('idle_discovery')['data']))
                self.assertEqual([row[2] for row in self.capture_calls()], [case('idle_discovery')['request']])
                if operation != 'status':
                    self.assertTrue(result['no_capture'])

    def test_changed_capture_id_or_invalid_discovery_never_retries_or_cleans_up(self):
        for operation in ('read', 'stop'):
            with self.subTest(operation=operation):
                self.client = CaptureClient(self.clock)
                self.client.replies[operation]['capture_id'] = OTHER_ID
                code, result, _ = self.execute([operation])
                self.assertEqual((code, result['reason']), (3, 'invalid_response'))
                self.assertEqual(result['unknown_outcome'], operation == 'stop')
                self.assertNotIn('data', result)
                self.assertEqual([row[2]['operation'] for row in self.capture_calls()], ['status', operation])
            for identity in (None, 'cap_short', PRIVATE_URL):
                self.client = CaptureClient(self.clock)
                self.client.replies['status']['capture_id'] = identity
                code, result, _ = self.execute([operation])
                self.assertEqual((code, result['reason']), (3, 'invalid_response'))
                self.assertFalse(result['unknown_outcome'])
                self.assertEqual([row[2]['operation'] for row in self.capture_calls()], ['status'])

    def test_unknown_mutations_retain_receipts_without_replay_or_automatic_stop(self):
        for operation in ('start', 'stop'):
            for stage in ('enqueue', 'receipt'):
                with self.subTest(operation=operation, stage=stage):
                    self.client = CaptureClient(self.clock)
                    failure = ott.RequestError('request_pending_timeout' if stage == 'receipt' else 'controller_unavailable',
                                               stage, PRIVATE_URL, 'c' * 32 if stage == 'receipt' else None)
                    failure.action = 'native_playback_diagnostics'
                    self.client.replies[operation] = failure
                    code, result, _ = self.execute([operation])
                    self.assertEqual(code, 3)
                    self.assertTrue(result['unknown_outcome'])
                    self.assertNotIn('data', result)
                    expected = ['start'] if operation == 'start' else ['status', 'stop']
                    self.assertEqual([row[2]['operation'] for row in self.capture_calls()], expected)
                    self.assertEqual(self.client.timeout, 20)
                    if stage == 'receipt':
                        self.assertEqual(result['requests'][-1]['request_id'], 'c' * 32)

    def test_typed_rejections_are_safe_terminal_outcomes(self):
        for row in FIXTURE['error_cases']:
            with self.subTest(name=row['name']):
                self.client = CaptureClient(self.clock)
                operation = row['request']['operation']
                failure = ott.PlayerUnsupported if row['status'] == 'unsupported' else ott.PlayerRejected
                self.client.replies[operation] = failure(PRIVATE_URL, row['data'])
                code, result, _ = self.execute([operation])
                self.assertEqual((code, result['verdict'], result['reason']), (1, 'error', row['data']['error']))
                self.assertFalse(result['unknown_outcome'])
                self.assertNotIn('data', result)
                self.assertEqual(result['requests'][-1]['state'], row['status'])
                self.assertEqual([call[2]['operation'] for call in self.capture_calls()],
                                 [operation] if operation in ('start', 'status') else ['status', operation])

    def test_terminal_capture_cannot_change_after_status_discovery(self):
        for state in FIXTURE['stop_states']:
            for operation in ('read', 'stop'):
                for mutation in ('state', 'elapsed_ms', 'dropped', 'fewer_records', 'more_records'):
                    with self.subTest(state=state, operation=operation, mutation=mutation):
                        self.client = CaptureClient(self.clock)
                        self.client.replies['status'] = case('status_' + state)['data']
                        self.client.replies[operation] = case(operation + '_' + state)['data']
                        data = self.client.replies[operation]
                        if mutation == 'state':
                            data['state'] = next(value for value in FIXTURE['stop_states'] if value != state)
                        elif mutation == 'elapsed_ms':
                            data['elapsed_ms'] = 2000
                        elif mutation == 'dropped':
                            data['dropped'] = 1
                        elif operation == 'read':
                            data['records'] = [] if mutation == 'fewer_records' else data['records'] * 2
                        else:
                            data['records'] = 0 if mutation == 'fewer_records' else 2
                        # Each reply is individually valid: this tests the
                        # cross-request terminal snapshot fence specifically.
                        self.assertEqual(capture.metadata(data, case(operation + '_' + state)['request']), data)
                        code, result, _ = self.execute([operation])
                        self.assertEqual((code, result['verdict'], result['reason']), (3, 'unknown', 'invalid_response'))
                        self.assertEqual(result['unknown_outcome'], operation == 'stop')
                        self.assertNotIn('data', result)
                        self.assertEqual(result['requests'][-1]['state'], 'unconfirmed')
                        self.assertEqual([row[2]['operation'] for row in self.capture_calls()], ['status', operation])

    def test_active_capture_can_progress_before_read_or_stop(self):
        for operation in ('read', 'stop'):
            with self.subTest(operation=operation):
                self.client = CaptureClient(self.clock)
                data = self.client.replies[operation]
                data.update(elapsed_ms=2000, dropped=1)
                data['records'] = ([data['records'][0], dict(data['records'][0], t_ms=2000)]
                                   if operation == 'read' else 2)
                code, result, _ = self.execute([operation])
                self.assertEqual((code, result['verdict'], result['data']), (0, 'observed', data))
                self.assertFalse(result['unknown_outcome'])
                self.assertEqual([row[2]['operation'] for row in self.capture_calls()], ['status', operation])

    def test_error_transport_status_operation_and_shape_must_match(self):
        request = case('start')['request']
        good = {'version': 1, 'operation': 'start', 'error': 'capture_busy'}
        for status, data in [('ok', good), ('unsupported', good),
                             ('rejected', dict(good, error='unsupported')),
                             ('rejected', dict(good, operation='stop')),
                             ('rejected', dict(good, message=PRIVATE_URL)),
                             ('rejected', dict(good, error=PRIVATE_URL))]:
            with self.subTest(status=status, data=data), self.assertRaises(capture.InvalidData):
                capture.metadata(data, request, status)

    def test_private_or_extra_fields_are_rejected_at_every_capture_level(self):
        for private in PRIVATE_VALUES:
            for field in ('url', 'token', 'profile', 'channel', 'programme', 'path', 'window_id'):
                for nested in (False, True):
                    with self.subTest(field=field, nested=nested):
                        self.client = CaptureClient(self.clock)
                        data = self.client.replies['read']
                        (data['records'][0] if nested else data)[field] = private
                        code, result, _ = self.execute(['read'])
                        self.assertEqual((code, result['reason']), (3, 'invalid_response'))
                        self.assertNotIn('data', result)
        for field in ('stage', 'load_state', 'feed_state'):
            self.client = CaptureClient(self.clock)
            self.client.replies['read']['records'][0][field] = PRIVATE_URL
            code, result, _ = self.execute(['read'])
            self.assertEqual((code, result['reason']), (3, 'invalid_response'))

    def test_record_numbers_use_exact_types_bounds_and_nullable_semantics(self):
        row = case('read_active')
        for name, rule in FIXTURE['record_fields'].items():
            if rule['type'] != 'integer':
                continue
            good = [rule['minimum'], rule['maximum']] + ([None] if rule['nullable'] else [])
            for value in good:
                data = copy.deepcopy(row['data'])
                data['elapsed_ms'] = 60000
                data['records'][0][name] = value
                self.assertEqual(capture.metadata(data, row['request']), data)
            bad = [True, False, 1.0, '1', float('nan'), float('inf'), rule['minimum'] - 1, rule['maximum'] + 1]
            if not rule['nullable']:
                bad.append(None)
            for value in bad:
                data = copy.deepcopy(row['data'])
                data['records'][0][name] = value
                with self.subTest(field=name, value=value), self.assertRaises(capture.InvalidData):
                    capture.metadata(data, row['request'])

    def test_record_limit_time_order_and_counter_resets(self):
        row = case('read_active')
        data = row['data']
        data['records'] = [dict(data['records'][0], t_ms=i, frames=64-i) for i in range(64)]
        self.assertEqual(capture.metadata(data, row['request']), data)
        for invalid in (dict(data, records=data['records'] + [data['records'][-1]]),
                        dict(data, records=list(reversed(data['records']))),
                        dict(data, elapsed_ms=62), dict(data, dropped=True), dict(data, elapsed_ms=60001)):
            with self.subTest(invalid=repr(invalid)[:100]), self.assertRaises(capture.InvalidData):
                capture.metadata(invalid, row['request'])
        data['records'] = [dict(data['records'][0], t_ms=0), dict(data['records'][1], t_ms=0)]
        self.assertEqual(capture.metadata(data, row['request']), data)

    def test_capture_route_and_passive_debug_remain_separate(self):
        code, result, _ = self.execute(['status'], route=True)
        self.assertEqual((code, result['command']), (0, 'debug capture'))
        self.assertEqual([row[1] for row in self.client.calls], ['capabilities', 'native_playback_diagnostics'])
        self.client.calls.clear()
        output = io.StringIO()
        with mock.patch.object(ott, 'native_playback_capture_module', return_value=capture), \
                contextlib.redirect_stdout(output):
            code = wb.run(ott, self.client, 'web', ['debug', '--native-playback'], 20, True)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['read_only'])
        self.assertEqual([row[1] for row in self.client.calls], ['capabilities', 'inspect'])

    def test_cancellation_before_dispatch_and_during_discovery_has_no_mutation(self):
        self.client.group_cancelled = threading.Event()
        self.client.group_cancelled.set()
        code, result, _ = self.execute(['start'])
        self.assertEqual(code, 130)
        self.assertFalse(result['unknown_outcome'])
        self.assertEqual(self.client.calls, [])
        self.client = CaptureClient(self.clock)
        self.client.replies['status'] = KeyboardInterrupt()
        code, result, _ = self.execute(['stop'])
        self.assertEqual(code, 130)
        self.assertFalse(result['unknown_outcome'])
        self.assertEqual([row[2]['operation'] for row in self.capture_calls()], ['status'])

    def test_cancellation_after_start_dispatch_does_not_replay_or_auto_stop(self):
        self.client.replies['start'] = KeyboardInterrupt()
        code, result, _ = self.execute(['start'])
        self.assertEqual(code, 130)
        self.assertTrue(result['unknown_outcome'])
        self.assertEqual([row[2]['operation'] for row in self.capture_calls()], ['start'])

    def test_shared_deadline_stops_before_capture_and_restores_client_timeout(self):
        original = self.client.call_receipt
        def slow_probe(target, action, params):
            result = original(target, action, params)
            self.clock.sleep(20)
            return result
        with mock.patch.object(self.client, 'call_receipt', side_effect=slow_probe):
            code, result, _ = self.execute(['start'])
        self.assertEqual((code, result['reason']), (3, 'deadline_exceeded'))
        self.assertFalse(result['unknown_outcome'])
        self.assertEqual([row[1] for row in self.client.calls], ['capabilities'])
        self.assertEqual(self.client.timeout, 20)

    def run_group(self, wait=None):
        args = SimpleNamespace(words=['t1,t2', 'debug', 'capture', 'start'], json=True, timeout=20, config='/unused')
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(ott, 'native_playback_capture_module', return_value=capture))
            stack.enter_context(mock.patch.object(capture, 'time', self.clock))
            if wait is not None:
                stack.enter_context(mock.patch.object(ott, 'wait', side_effect=wait))
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(contextlib.redirect_stderr(errors))
            code = ott.player_group_command(self.client, self.client.config, [('t1', 'web'), ('t2', 'web2')], args, [])
        text = output.getvalue() + errors.getvalue()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(PRIVATE_URL, text)
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_group_executes_capture_requests_in_parallel_and_isolates_output(self):
        self.client.capture_barrier = threading.Barrier(2)
        code, result, errors = self.run_group()
        self.assertEqual((code, result['succeeded'], result['failed']), (0, 2, 0))
        self.assertEqual([row['player'] for row in result['results']], ['t1', 't2'])
        for row in result['results']:
            self.assertEqual(row['data']['data'], case('start')['data'])
        for child in self.client.children:
            self.assertEqual([row[1] for row in child.calls], ['capabilities', 'native_playback_diagnostics'])
            self.assertEqual(child.calls[-1][2]['operation'], 'start')
        self.assertEqual(errors, '')

    def test_group_cancellation_stops_waiting_workers_without_replay_or_cleanup(self):
        entered, interrupted = threading.Event(), threading.Event()
        lock = threading.Lock()
        pending = []
        original_wait = ott.wait
        def block(child, params):
            self.assertEqual(params['operation'], 'start')
            with lock:
                pending.append(child)
                if len(pending) == 2:
                    entered.set()
            self.assertTrue(child.group_cancelled.wait(5), 'group failed to cancel its capture worker')
            raise KeyboardInterrupt
        def interrupt(*args, **kwargs):
            if not interrupted.is_set():
                self.assertTrue(entered.wait(5), 'both capture workers must reach their request')
                interrupted.set()
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)
        self.client.capture_hook = block
        code, result, _ = self.run_group(wait=interrupt)
        self.assertEqual((code, result['failed']), (130, 2))
        self.assertEqual([row['exit_code'] for row in result['results']], [130, 130])
        for child in self.client.children:
            self.assertTrue(child.group_cancelled.is_set())
            self.assertEqual([row[2]['operation'] for row in child.calls if row[1] == capture.ACTION], ['start'])


class NativeCaptureRawReceiptTest(unittest.TestCase):
    def call(self, raw, request=None):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 20
        client.api = mock.Mock(return_value=(202, {'id': 'a' * 32}))
        client.api_raw = mock.Mock(return_value=(200, raw))
        self.client = client
        with mock.patch.object(ott, 'native_playback_capture_module', return_value=capture), \
                mock.patch.object(ott.time, 'sleep'):
            return client.call_receipt('web', 'native_playback_diagnostics', request or case('start')['request'])

    def wire(self, name='start'):
        return json.dumps({'id': 'a' * 32, 'status': 'ok', 'data': case(name)['data']},
                          separators=(',', ':')).encode()

    def test_raw_outer_byte_limit_includes_whitespace(self):
        raw = self.wire()
        padded = raw + b' ' * (65536-len(raw))
        self.assertEqual(self.call(padded)['data'], case('start')['data'])
        self.assertEqual((self.client.api.call_count, self.client.api_raw.call_count), (1, 1))
        with self.assertRaises(ott.RequestError) as caught:
            self.call(padded + b' ')
        self.assertEqual((caught.exception.reason, caught.exception.request_id), ('invalid_response', 'a' * 32))
        self.assertEqual((self.client.api.call_count, self.client.api_raw.call_count), (1, 1))

    def test_raw_duplicate_keys_and_crossed_receipts_never_replay(self):
        start, read = self.wire(), self.wire('read_active')
        failures = [(start.replace(b'"status":"ok"', b'"status":"ok","status":"ok"'), 'start'),
                    (start.replace(b'"state":"capturing"', b'"state":"capturing","state":"capturing"'), 'start'),
                    (read.replace(b'"frames":7', b'"frames":7,"frames":7'), 'read_active'),
                    (read.replace(b'"frames":7', b'"frames":NaN'), 'read_active'),
                    (start.replace(b'"id":"' + b'a'*32, b'"id":"' + b'b'*32), 'start')]
        for raw, name in failures:
            with self.subTest(name=name), self.assertRaises(ott.RequestError) as caught:
                self.call(raw, case(name)['request'])
            self.assertEqual(caught.exception.request_id, 'a' * 32)
            self.assertEqual((self.client.api.call_count, self.client.api_raw.call_count), (1, 1))


if __name__ == '__main__':
    unittest.main()
