"""Native folder tokens are fenced, explicit and never replayed across players."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_local_browser', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
FIXTURE = json.loads((ROOT / 'testdata/local_browser_v1.json').read_text())


def caps(runtime='native-qa-1'):
    return {'version': 1, 'player': {'version': 'test', 'platform': 'native', 'runtime': runtime},
            'lifecycle': [], 'input': [], 'playback': [],
            'local_browser': copy.deepcopy(FIXTURE['capabilities']['local_browser'])}


def case(name):
    return copy.deepcopy(next(row for row in FIXTURE['browser_cases'] if row['name'] == name))


def words(request):
    op = request['op']
    if op == 'roots':
        return ['local', 'roots']
    selector = (['--cursor', request['cursor']] if 'cursor' in request else
                [request['entry_id'] if op == 'list' else request['select_id']])
    return ['local', 'browse' if op == 'list' else op, *selector, '--generation', request['generation']]


class LocalBrowserTest(unittest.TestCase):
    def run_cli(self, arguments, replies, machine=True):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = replies
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['-j'] if machine else []) + ['tv', *arguments])
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client.call.call_args_list

    def test_shared_wire_examples_and_single_dispatch(self):
        self.assertEqual(ott.MAX_LOCAL_BROWSER_ENTRIES, 64)
        for row in FIXTURE['browser_cases']:
            with self.subTest(name=row['name']):
                reply = row['data'] if row['status'] == 'ok' else ott.PlayerRejected('private detail', row['data'])
                code, out, err, calls = self.run_cli(words(row['request']), [caps(), reply])
                self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                        mock.call('dev_tv', 'local_browser', row['request'])])
                self.assertEqual(code, 0 if row['status'] == 'ok' else 1)
                if code == 0:
                    self.assertEqual(json.loads(out), row['data'])
                else:
                    self.assertEqual(out, '')
                    self.assertIn('not repeated', err)
                    self.assertNotIn('private detail', err)

    def test_parser_preserves_tokens_and_legacy_list(self):
        self.assertEqual(ott.parse_command(['local', 'list']), ('local_media', {'op': 'sources'}))
        self.assertEqual(ott.parse_command(['OFFLINE', 'BROWSE', '--generation', 'Gen_A:1', 'Entry-X']),
                         ('local_browser', {'op': 'list', 'generation': 'Gen_A:1', 'entry_id': 'Entry-X'}))
        self.assertIn('local_browser', ott.RECEIPT_ACTIONS)
        invalid = [[], ['roots', 'extra'], ['roots', '--generation', 'g'], ['browse', 'e'],
                   ['browse', 'e', '--generation', 'g', '--cursor', 'c'],
                   ['browse', '--cursor', 'c', '--generation', 'g', '--generation', 'g'],
                   ['select', '--cursor', 'c', '--generation', 'g'],
                   ['more', 's', '--generation'], ['more', 's', '--generation', 'g', 'extra']]
        for args in invalid[1:]:
            self.assertEqual(self.run_cli(['local', *args], [])[0::3], (1, []))
        for token in ('', '/tmp/usb', '../Movies', 'a b', '\ud800', 'é', 'a' * 257):
            self.assertEqual(self.run_cli(['local', 'select', token, '--generation', 'g'], [])[0::3], (1, []))

    def test_missing_or_malformed_capability_prevents_browser_request(self):
        base = caps()['local_browser']
        markers = [None, {}, True, dict(base, version=True), dict(base, version=2),
                   dict(base, max_entries=63), dict(base, max_entries=True),
                   dict(base, operations=['roots']), dict(base, operations=['roots'] * 4),
                   dict(base, operations=['roots', 'list', 'more', []]), dict(base, private='secret')]
        missing = caps(); del missing['local_browser']
        for value in [missing] + [dict(caps(), local_browser=marker) for marker in markers]:
            code, out, err, calls = self.run_cli(['local', 'roots'], [value])
            self.assertEqual((code, out, calls), (1, '', [mock.call('dev_tv', 'capabilities', {})]))
            self.assertNotIn('secret', err)
        reordered = dict(base, operations=list(reversed(base['operations'])))
        self.assertEqual(ott.capabilities_metadata(dict(caps(), local_browser=reordered))['local_browser'], reordered)

    def test_views_are_closed_bounded_and_generation_fenced(self):
        row = case('directory_page'); data, request = row['data'], row['request']
        changes = [lambda d: d.update(version=True), lambda d: d.update(runtime='wrong'),
                   lambda d: d.update(op='more'), lambda d: d.update(generation=request['generation']),
                   lambda d: d.update(generation=''), lambda d: d.update(private='secret'),
                   lambda d: d.update(partial=1), lambda d: d.update(limit_reached=True),
                   lambda d: d.update(select_id=None), lambda d: d.update(parent_id='/path'),
                   lambda d: d.update(next_cursor=True), lambda d: d.update(entries=d['entries'] * 2),
                   lambda d: d['entries'][0].update(kind='root'),
                   lambda d: d['entries'][0].update(path='/private/secret'),
                   lambda d: d['entries'][0].update(name='x\x85y')]
        for change in changes:
            bad = copy.deepcopy(data); change(bad)
            with self.subTest(bad=bad), self.assertRaises(ott.Error):
                ott.local_browser_metadata(bad, request['runtime'], request)
        bad = dict(data, entries=[dict(data['entries'][0], id=str(n)) for n in range(65)])
        with self.assertRaises(ott.Error):
            ott.local_browser_metadata(bad, request['runtime'], request)
        # A compact JSON boundary includes escaping, not just decoded names.
        bad = dict(data, entries=[{'id': str(n) + 'x' * 250, 'name': '"' * 512, 'kind': 'directory'} for n in range(64)])
        with self.assertRaises(ott.Error):
            ott.local_browser_metadata(bad, request['runtime'], request)

    def test_display_names_are_not_path_authority_and_partial_is_independent(self):
        row = case('directory_page'); data, request = row['data'], row['request']
        for name in (' ', '.', '..', 'USB/SSD', 'a\\b', 'é' * 256):
            data['entries'][0]['name'] = name
            self.assertEqual(ott.local_browser_metadata(data, request['runtime'], request), data)
        for name in ('', '\x00', '\x1f', '\x7f', '\x85', '\ud800', 'é' * 257):
            data['entries'][0]['name'] = name
            with self.assertRaises(ott.Error):
                ott.local_browser_metadata(data, request['runtime'], request)
        data['entries'] = []
        for partial, cursor, reached in ((False, None, False), (False, 'c', False),
                                         (True, None, False), (True, 'c', False), (True, None, True)):
            data.update(partial=partial, next_cursor=cursor, limit_reached=reached)
            self.assertEqual(ott.local_browser_metadata(data, request['runtime'], request), data)
        roots = case('available_roots')['data']
        roots.update(op='list', generation='new-roots', partial=True)
        self.assertEqual(ott.local_browser_metadata(roots, request['runtime'], request), roots)

    def test_select_requires_same_generation_no_save_no_activation(self):
        row = case('select_without_playback_or_save'); data, request = row['data'], row['request']
        changes = [lambda d: d.update(generation='another'), lambda d: d.update(persisted=True),
                   lambda d: d.update(activated=True), lambda d: d.update(activated=0),
                   lambda d: d.update(playback_confirmed=True), lambda d: d['source'].update(handle='/path'),
                   lambda d: d['source'].update(stable_identity=1), lambda d: d['source'].update(kind='file'),
                   lambda d: d['source'].update(path='/private/secret')]
        for change in changes:
            bad = copy.deepcopy(data); change(bad)
            with self.assertRaises(ott.Error):
                ott.local_browser_metadata(bad, request['runtime'], request)
        data['source']['stable_identity'] = False
        code, out, err, _ = self.run_cli(words(request), [caps(), data], machine=False)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Not saved or activated; playback was not started', out)
        self.assertIn('not guaranteed', out)

    def test_closed_errors_never_leak_details_or_replay(self):
        row = case('roots_busy'); request = row['request']
        for error in ott.LOCAL_BROWSER_ERRORS:
            data = dict(row['data'], error=error)
            self.assertEqual(ott.local_browser_metadata(data, request['runtime'], request, status='rejected'), data)
            with self.assertRaises(ott.Error):
                ott.local_browser_metadata(data, request['runtime'], request)
        ready = case('available_roots')['data']
        with self.assertRaises(ott.Error):
            ott.local_browser_metadata(ready, request['runtime'], request, status='rejected')
        for bad in (dict(row['data'], error='private secret'), dict(row['data'], detail='secret'),
                    dict(row['data'], generation='g'), dict(row['data'], runtime='wrong')):
            code, out, err, calls = self.run_cli(words(request), [caps(), ott.PlayerRejected('secret', bad)])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('secret', err)
        for failure in (ott.TransportError('uncertain'), ott.RequestError('receipt_missing', 'receipt', 'uncertain'),
                        ott.HTTPError(400), ott.PlayerUnsupported('unsupported')):
            self.assertEqual(len(self.run_cli(words(request), [caps(), failure])[3]), 2)
            self.assertEqual(len(self.run_cli(words(request), [failure])[3]), 1)
        for status in (400, 404):
            for sent in (False, True):
                replies = ([caps()] if sent else []) + [ott.HTTPError(status)]
                code, out, err, calls = self.run_cli(words(request), replies)
                self.assertEqual((code, out, len(calls)), (1, '', 2 if sent else 1))
                self.assertIn('update the controller and player', err)
                self.assertIn('not repeated' if sent else 'No local browser command was sent', err)

    def test_whole_deadline_and_params_are_preserved(self):
        row = case('available_roots'); params = {'op': 'roots'}
        now, observed = [0], []
        client = mock.Mock(timeout=45)
        def call(device, action, request):
            observed.append((action, client.timeout))
            now[0] += 4
            return caps() if action == 'capabilities' else row['data']
        client.call.side_effect = call
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]):
            self.assertEqual(ott.local_browser_command(client, 'dev_tv', params, 10), row['data'])
        self.assertEqual(observed, [('capabilities', 10), ('local_browser', 6)])
        self.assertEqual((params, client.timeout), ({'op': 'roots'}, 45))
        for spent, count in ((11, 1), (6, 2)):
            now[0] = 0; client.call.reset_mock()
            def slow(device, action, request):
                now[0] += spent
                return caps() if action == 'capabilities' else row['data']
            client.call.side_effect = slow
            with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]), self.assertRaises(ott.Error):
                ott.local_browser_command(client, 'dev_tv', params, 10)
            self.assertEqual(client.call.call_count, count)
            self.assertEqual(client.timeout, 45)

    def test_help_is_only_advertised_for_supported_operations(self):
        old = caps(); del old['local_browser']
        for value, present in ((old, False), (caps(), True)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ott.print_player_status({'capabilities': value}, 'TV')
            for action in ('roots', 'browse', 'more', 'select'):
                self.assertEqual(' local ' + action in out.getvalue(), present)
            self.assertEqual('No controls are currently advertised' in out.getvalue(), not present)

    def test_group_tokens_fail_before_any_request_and_roots_run_in_parallel(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'t1': 'dev_1', 't2': 'dev_2'}, 'groups': {'tt': ['t1', 't2']}}
        client.credentials = {'devices': [{'id': 'dev_1'}, {'id': 'dev_2'}]}
        client.server, client.timeout, client.on_receipt = 'https://controller.example', 45, None
        client.call = mock.Mock()
        for arguments in (['local', 'browse', 'e', '--generation', 'g'],
                          ['local', 'more', 's', '--generation', 'g'],
                          ['local', 'select', 's', '--generation', 'g']):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(ott, 'Client', return_value=client), \
                    mock.patch.object(ott, 'read_json', return_value=client.config), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = ott.main(['t1,t2', *arguments])
            self.assertEqual((code, out.getvalue(), client.call.call_count), (1, '', 0))
        barrier = threading.Barrier(2)
        def reply(device, action, params):
            runtime = device.replace('_', '-')
            if action == 'capabilities':
                barrier.wait(timeout=3)
                return caps(runtime)
            self.assertEqual(params, {'op': 'roots', 'version': 1, 'runtime': runtime})
            return dict(case('available_roots')['data'], runtime=runtime)
        client.call.side_effect = reply
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value=client.config), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(['-j', 't1,t2', 'local', 'roots'])
        self.assertEqual((code, err.getvalue()), (0, ''))
        self.assertEqual((json.loads(out.getvalue())['succeeded'], client.call.call_count), (2, 4))


if __name__ == '__main__':
    unittest.main()
