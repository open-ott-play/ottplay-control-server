import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_plex_queue', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

IDS = ['78777', '78776', '78775']
SECRET = 'private-token-never-print'


def caps():
    return {'version': 1, 'player': {'version': '1.1.54', 'platform': 'webos', 'runtime': 'page-123'},
            'lifecycle': [], 'input': [], 'playback': ['previous_channel', 'next_channel'],
            'plex_queue': {'version': 1, 'operations': ['play', 'preview', 'status', 'next', 'previous', 'stop'], 'max_items': 100}}


def queue(state='playing', index=0):
    ids = [] if state == 'idle' else list(IDS)
    return {'version': 1, 'runtime': 'page-123', 'active': bool(ids), 'state': state,
            'ids': ids, 'index': index if ids else None, 'repeat': 'none', 'order': 'listed'}


def preview():
    return {'version': 1, 'runtime': 'page-123', 'state': 'ready', 'ids': list(IDS),
            'titles': ['Film 1', 'Film 2', 'Film 3'], 'order': 'listed'}


class PlexQueueTest(unittest.TestCase):
    def test_shared_contract_examples_and_error_allowlist(self):
        contract = json.loads((Path(__file__).resolve().parents[1] / 'contracts/plex-queue-v1.json').read_text())
        self.assertEqual(set(contract['errors']), ott.PLEX_QUEUE_ERRORS)
        self.assertEqual(set(contract['operations']), ott.PLEX_QUEUE_OPERATIONS)
        self.assertEqual(contract['max_items'], ott.MAX_PLEX_QUEUE_ITEMS)
        for key in ['queue_result', 'ended_result', 'stop_result']:
            self.assertEqual(ott.plex_queue_metadata(contract[key]), contract[key])
        self.assertEqual(ott.plex_queue_metadata(contract['preview_result'], 'page-123',
                                               contract['preview_request']['params']), contract['preview_result'])

    def run_cli(self, words, responses, json_output=False):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = responses
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = ott.main((['--json'] if json_output else []) + ['tv'] + words)
        self.assertEqual(client.timeout, 45)
        return result, output.getvalue(), errors.getvalue(), client.call.call_args_list

    def test_explicit_order_and_case_insensitive_commands(self):
        for verb in ['play', 'PlAy', 'preview', 'PREVIEW']:
            op = verb.lower()
            value = preview() if op == 'preview' else queue()
            code, out, err, calls = self.run_cli(['PlEx', verb] + IDS, [caps(), value], True)
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(json.loads(out), value)
            self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                    mock.call('dev_tv', 'plex_queue', {'op': op, 'ids': IDS, 'runtime': 'page-123'})])

    def test_status_step_stop_aliases_and_boundary_error(self):
        for word, op in [('queue', 'status'), ('STATUS', 'status'), ('next', 'next'), ('prev', 'previous'),
                         ('previous', 'previous'), ('STOP', 'stop')]:
            code, out, err, calls = self.run_cli(['plex', word], [caps(), queue('idle') if op == 'stop' else queue()])
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(calls[-1], mock.call('dev_tv', 'plex_queue', {'op': op, 'runtime': 'page-123'}))
        error = 'Plex queue is already at its last item.'
        code, out, err, calls = self.run_cli(['plex', 'next'], [caps(), ott.PlayerRejected(SECRET, {'error': error})])
        self.assertEqual((code, out, len(calls)), (1, '', 2))
        self.assertIn(error, err)
        self.assertNotIn(SECRET, err)

    def test_invalid_ids_and_syntax_send_nothing(self):
        invalid = [[], ['0'], ['01'], ['-1'], ['+1'], ['1.0'], ['1e3'], ['１２'], ['1' * 21],
                   ['https://private/' + SECRET], ['Film title'], ['1'] * 101]
        for ids in invalid:
            for verb in ['play', 'preview']:
                code, out, err, calls = self.run_cli(['plex', verb] + ids, [])
                self.assertEqual((code, out, calls), (1, '', []))
                self.assertNotIn(SECRET, err)
        for words in [['plex', 'status', '1'], ['plex', 'stop', 'now'], ['plex', 'queue', 'next']]:
            self.assertEqual(self.run_cli(words, [])[3], [])
        self.assertEqual(ott.parse_command(['plex', 'play', '1', '1'])[1]['ids'], ['1', '1'])
        self.assertEqual(len(ott.parse_command(['plex', 'play'] + ['9' * 20] * 100)[1]['ids']), 100)

    def test_unsupported_player_and_malformed_caps_never_mutate(self):
        old = caps(); old.pop('plex_queue')
        malformed = caps(); malformed['plex_queue']['operations'].append('shell')
        duplicate = caps(); duplicate['plex_queue']['operations'].append('play')
        wrong_bound = caps(); wrong_bound['plex_queue']['max_items'] = True
        for value in [old, malformed, duplicate, wrong_bound, ott.PlayerUnsupported(SECRET)]:
            code, out, err, calls = self.run_cli(['plex', 'play'] + IDS, [value])
            self.assertEqual((code, out, len(calls)), (1, '', 1))
            self.assertNotIn(SECRET, err)

    def test_invalid_or_stale_receipts_never_claim_success_or_replay(self):
        mutations = [lambda v: v.update(runtime='other-page'), lambda v: v.update(ids=list(reversed(IDS))),
                     lambda v: v.update(index=1), lambda v: v.update(index=True), lambda v: v.update(active=1),
                     lambda v: v.update(repeat='all'), lambda v: v.update(order='random'),
                     lambda v: v.update(url='https://private/' + SECRET), lambda v: v.update(error=SECRET),
                     lambda v: v.update(title='x' * 513), lambda v: v.update(title='bad\ntext'),
                     lambda v: v.update(ids=['1'] * 101), lambda v: v.pop('index')]
        for mutate in mutations:
            value = queue(); mutate(value)
            code, out, err, calls = self.run_cli(['plex', 'play'] + IDS, [caps(), value], True)
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertIn('do not repeat', err)
            self.assertNotIn(SECRET, err)

    def test_preview_errors_and_titles_are_bounded(self):
        data = preview()
        data.update(ids=['1'] * 100, titles=['"\\' * 256] * 100)
        self.assertGreater(len(json.dumps(data)), 65536)
        self.assertEqual(ott.plex_queue_metadata(data, 'page-123', {'op': 'preview', 'ids': ['1'] * 100}), data)
        for key, value in [('titles', ['wrong length']), ('titles', ['x' * 513] * 3), ('title', SECRET),
                           ('runtime', 'other'), ('error', SECRET), ('state', 'playing')]:
            data = preview(); data[key] = value
            code, out, err, calls = self.run_cli(['plex', 'preview'] + IDS, [caps(), data])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)
        data = preview(); data.update(state='error', titles=[], error='Plex server is unreachable or access was denied.')
        code, out, err, calls = self.run_cli(['plex', 'preview'] + IDS, [caps(), data])
        self.assertEqual((code, out, len(calls)), (1, '', 2))
        self.assertIn(data['error'], err)

    def test_preparing_and_retained_ended_are_reported_honestly(self):
        code, out, err, _ = self.run_cli(['plex', 'play'] + IDS, [caps(), queue('preparing')])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('playback has not been confirmed', out)
        code, out, err, _ = self.run_cli(['plex', 'status'], [caps(), queue('ended', 2)], True)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual((json.loads(out)['state'], json.loads(out)['active']), ('ended', True))

    def test_generic_step_uses_atomic_player_reply_and_preserves_tv(self):
        for word, operation in [('next', 'next_channel'), ('prev', 'previous_channel')]:
            for destination in [{'plex_queue': queue(index=1)}, {'channel': {'id': 'channel-2', 'number': 2, 'name': 'TV'}}]:
                value = dict(operation=operation, dispatched=True, **destination)
                code, out, err, calls = self.run_cli([word], [value], True)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(json.loads(out), value)
                self.assertEqual(calls, [mock.call('dev_tv', 'playback', {'operation': operation})])
        with self.assertRaises(ott.Error):
            ott.playback_metadata({'operation': 'next_channel', 'dispatched': True, 'plex_queue': queue(),
                                   'channel': {'id': '2', 'name': 'TV', 'number': 2}}, {'operation': 'next_channel'})
        message = 'Plex queue is already at its last item.'
        code, out, err, calls = self.run_cli(['next'], [ott.PlayerRejected(SECRET, {'error': message})])
        self.assertEqual((code, out, len(calls)), (1, '', 1))
        self.assertIn(message, err)
        self.assertNotIn(SECRET, err)

    def test_capability_listing_and_total_deadline(self):
        data = {'capabilities': ott.capabilities_metadata(caps())}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_status(data, 'tv')
        self.assertIn('plex play ID', out.getvalue())
        client = mock.Mock(timeout=45)
        client.call.return_value = caps()
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 46]), self.assertRaisesRegex(ott.Error, 'no queue command'):
            ott.plex_queue_command(client, 'dev_tv', {'op': 'play', 'ids': IDS}, 45)
        self.assertEqual(client.call.call_count, 1)
        self.assertEqual(client.timeout, 45)

    def test_transport_uncertainty_and_rejections_never_replay_or_echo_payload(self):
        for failure in [ott.PlayerRejected(SECRET, {'error': SECRET}), ott.PlayerUnsupported(SECRET),
                        ott.Error('No receipt; do not repeat blindly')]:
            code, out, err, calls = self.run_cli(['plex', 'play'] + IDS, [caps(), failure])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)
        client = mock.Mock(timeout=45)
        client.call.side_effect = [caps(), queue()]
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 1, 46]), self.assertRaisesRegex(ott.Error, 'Acceptance is uncertain'):
            ott.plex_queue_command(client, 'dev_tv', {'op': 'play', 'ids': IDS}, 45)
        self.assertEqual(client.call.call_count, 2)
        self.assertEqual(client.timeout, 45)


if __name__ == '__main__':
    unittest.main()
