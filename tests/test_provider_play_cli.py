"""Provider playback waits for the requested source and uses one bound mutation."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_provider_play', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class ProviderPlayTest(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.calls = []
        self.timeouts = []
        self.provider = 'm3u'
        self.rows = [{'id': 'one', 'number': 1, 'name': 'РЕН'},
                     {'id': 'two', 'number': 2, 'name': 'РЕН ТВ HD'},
                     {'id': 'seven', 'number': 7, 'name': 'Кино'}]
        self.providers = [{'id': 'm3u', 'index': 0, 'name': 'M3U playlist'},
                          {'id': 'offline', 'index': 1, 'name': 'Local videos'},
                          {'id': 'plex', 'index': 2, 'name': 'Plex'},
                          {'id': 'iptv-ott.ru', 'index': 3, 'name': 'IPTV OTT'},
                          {'id': 'bestlist/stalker', 'index': 4, 'name': 'Best list'}]
        self.statuses = []
        self.overrides = {}
        self.client = ott.Client.__new__(ott.Client)
        self.client.config = {'players': {'t1': 'dev1', 't2': 'dev2'}}
        self.client.credentials = {'devices': [{'id': 'dev1'}, {'id': 'dev2'}]}
        self.client.timeout = 5
        self.client.call = mock.Mock(side_effect=self.call)
        self.patchers = [mock.patch.object(ott.time, 'monotonic', side_effect=lambda: self.clock),
                         mock.patch.object(ott.time, 'sleep', side_effect=self.sleep)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def sleep(self, duration):
        self.clock += duration

    def status(self, provider=None, ready=True, standby=False):
        return {'provider': provider or self.provider, 'ready': ready, 'player': {'standby': standby}}

    def capabilities(self, modern=False):
        data = {'version': 1, 'player': {'version': 'test', 'platform': 'browser', 'runtime': 'runtime1'},
                'lifecycle': ['wake'], 'input': [], 'playback': [], 'random': {'version': 1}}
        if modern:
            data['categories'] = {'version': 1, 'operations': ['list', 'select'],
                                  'maxPageSize': 2}
        return data

    def call(self, device, action, params):
        self.calls.append((device, action, params))
        self.timeouts.append(self.client.timeout)
        self.clock += 0.01
        if action in self.overrides:
            value = self.overrides[action]
            if callable(value):
                return value(device, params)
            if isinstance(value, Exception):
                raise value
            return value
        if action == 'providers':
            return {'providers': self.providers}
        if action == 'provider':
            self.provider = params['query']
            return {'dispatched': True, 'provider': self.provider}
        if action == 'status':
            return self.statuses.pop(0) if self.statuses else self.status()
        if action == 'capabilities':
            return self.capabilities()
        if action == 'epg_catalog':
            return {'catalog': 'source-snapshot', 'channels': self.rows}
        if action == 'lifecycle':
            return {'operation': 'wake', 'accepted': True, 'dispatched': False, 'effect': 'lifecycle-after-ack'}
        if action == 'play_catalog':
            if 'random' in params:
                return {'dispatched': True, 'channel': self.rows[-1]}
            return {'dispatched': True, 'channel': next(row for row in self.rows if row['id'] == params['id'])}
        raise AssertionError((action, params))

    def run_cli(self, words, target='t1'):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value=self.client.config), \
                mock.patch.object(ott, 'Client', return_value=self.client), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(['--json', target] + words)
        return code, json.loads(output.getvalue()) if output.getvalue() else None, errors.getvalue()

    def mutations(self):
        return [(device, action, params) for device, action, params in self.calls
                if action in ('provider', 'lifecycle', 'play_catalog', 'play', 'command')]

    def test_number_waits_for_source_then_plays_bound_catalog_once(self):
        self.statuses = [self.status('old', False), self.status('m3u', False), self.status()]
        code, data, err = self.run_cli(['provider', 'm3u', '7'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(data['playback']['channel'], self.rows[2])
        self.assertEqual(self.mutations(), [('dev1', 'provider', {'query': 'm3u'}),
            ('dev1', 'play_catalog', {'catalog': 'source-snapshot', 'id': 'seven'})])
        self.assertEqual([action for _, action, _ in self.calls],
                         ['providers', 'provider', 'status', 'status', 'status', 'capabilities',
                          'epg_catalog', 'status', 'play_catalog'])
        self.assertEqual(self.client.timeout, 5)
        self.assertEqual(self.timeouts, sorted(self.timeouts, reverse=True))

    def test_exact_name_precedes_partial_matches_case_insensitively(self):
        with mock.patch.object(ott.secrets, 'choice') as choice:
            code, data, _ = self.run_cli(['prov', 'm3u', 'рен'])
        self.assertEqual(code, 0)
        self.assertEqual(data['playback']['channel']['id'], 'one')
        choice.assert_not_called()

    def test_partial_names_and_duplicate_exact_names_choose_only_eligible_rows(self):
        for query, rows in [('ре', self.rows[:2]), ('РЕН', [self.rows[0], dict(self.rows[1], name='рен')])]:
            with self.subTest(query=query):
                self.rows = rows
                with mock.patch.object(ott.secrets, 'choice', side_effect=lambda candidates: candidates[-1]) as choice:
                    code, data, _ = self.run_cli(['provider', 'm3u', query])
                self.assertEqual(code, 0)
                choice.assert_not_called()
                self.assertEqual(self.mutations()[-1][2], {'catalog': 'source-snapshot', 'random': {'search': query}})
                self.assertEqual(data['playback']['channel']['id'], 'two')

    def test_random_aliases_draw_from_complete_catalog(self):
        for word in ('R', 'r', 'random', 'RANDOM'):
            with self.subTest(word=word), mock.patch.object(ott.secrets, 'choice', side_effect=lambda rows: rows[-1]) as choice:
                code, data, _ = self.run_cli(['provider', 'm3u', word])
                self.assertEqual(code, 0)
                choice.assert_not_called()
                self.assertEqual(self.mutations()[-1][2], {'catalog': 'source-snapshot', 'random': {}})
                self.assertEqual(data['playback']['channel']['id'], 'seven')

    def test_multiword_provider_queries_and_explicit_boundary(self):
        for words in (['provider', 'Local', 'videos'], ['provider', 'Local', 'vid'], ['prov', 'Local videos']):
            with self.subTest(words=words):
                self.calls.clear()
                code, data, _ = self.run_cli(words)
                self.assertEqual(code, 0)
                self.assertNotIn('playback', data)
                self.assertEqual(len(self.mutations()), 1)
        for words in (['provider', 'Local', 'videos', '1'], ['provider', 'Local videos', '--', '1'],
                      ['provider', '1', '1']):
            with self.subTest(words=words):
                code, data, _ = self.run_cli(words)
                self.assertEqual(code, 0)
                self.assertEqual(data['provider'], 'offline')
                self.assertEqual(data['playback']['channel']['id'], 'one')

    def test_full_tail_provider_ambiguity_never_becomes_playback(self):
        self.providers += [{'id': 'other', 'index': 8, 'name': 'Other local videos'}]
        code, _, err = self.run_cli(['provider', 'local', 'vid'])
        self.assertEqual(code, 1)
        self.assertIn('Several providers', err)
        self.assertEqual(self.mutations(), [])

    def test_explicit_boundary_overrides_full_provider_name_collision(self):
        self.providers += [{'id': 'collision', 'index': 9, 'name': 'm3u РЕН'}]
        code, data, _ = self.run_cli(['provider', 'm3u', '--', 'РЕН'])
        self.assertEqual(code, 0)
        self.assertEqual(data['provider'], 'm3u')
        self.assertEqual(data['playback']['channel']['id'], 'one')

    def test_dotted_and_slash_provider_ids_work(self):
        for provider in ('iptv-ott.ru', 'bestlist/stalker'):
            with self.subTest(provider=provider):
                code, data, _ = self.run_cli(['provider', provider, '1'])
                self.assertEqual(code, 0)
                self.assertEqual(data['provider'], provider)

    def test_no_match_keeps_acknowledged_provider_and_never_plays(self):
        code, data, err = self.run_cli(['provider', 'm3u', 'missing'])
        self.assertEqual(code, 1)
        self.assertEqual(data['provider'], 'm3u')
        self.assertEqual(data['playback']['stage'], 'selection')
        self.assertIs(data['playback']['dispatched'], False)
        self.assertIn('No channels match', err)
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])

    def test_changed_source_after_catalog_never_plays(self):
        self.statuses = [self.status(), self.status('plex')]
        code, data, _ = self.run_cli(['provider', 'm3u', '1'])
        self.assertEqual(code, 1)
        self.assertIs(data['playback']['dispatched'], False)
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])

    def test_unknown_provider_or_play_acceptance_never_retries(self):
        self.overrides['provider'] = ott.Error('The request may have been accepted; do not repeat')
        code, data, _ = self.run_cli(['provider', 'm3u', '1'])
        self.assertEqual((code, data), (1, None))
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])
        self.calls.clear()
        self.overrides.pop('provider')
        self.overrides['play_catalog'] = ott.Error('The request may have been accepted; do not repeat')
        code, data, _ = self.run_cli(['provider', 'm3u', '1'])
        self.assertEqual(code, 1)
        self.assertIsNone(data['playback']['dispatched'])
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider', 'play_catalog'])

    def test_standby_wakes_once_then_waits_for_actual_completion(self):
        self.statuses = [self.status('old', False, True), self.status('m3u', False, True),
                         self.status('m3u', False), self.status()]
        code, data, err = self.run_cli(['provider', 'm3u', '1'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(data['wake']['operation'], 'wake')
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider', 'lifecycle', 'play_catalog'])

    def test_unknown_or_invalid_wake_never_retries_or_plays(self):
        for result in (ott.Error('Wake may have executed'), {'accepted': True}):
            with self.subTest(result=result):
                self.calls.clear()
                self.statuses = [self.status(standby=True)]
                self.overrides['lifecycle'] = result
                code, data, err = self.run_cli(['provider', 'm3u', '1'])
                self.assertEqual(code, 1)
                self.assertIs(data['playback']['dispatched'], False)
                self.assertIn('Wake was not confirmed', err)
                self.assertEqual([action for _, action, _ in self.mutations()], ['provider', 'lifecycle'])

    def test_timeout_after_switch_has_no_catalog_or_play_and_restores_budget(self):
        self.overrides['status'] = self.status(ready=False)
        code, data, err = self.run_cli(['provider', 'm3u', '1'])
        self.assertEqual(code, 1)
        self.assertEqual(data['playback']['stage'], 'waiting')
        self.assertIn('exceeded --timeout', err)
        self.assertEqual(self.client.timeout, 5)
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])

    def test_group_keeps_per_player_partial_results(self):
        self.overrides['play_catalog'] = lambda device, params: (
            {'dispatched': True, 'channel': self.rows[0]} if device == 'dev1'
            else (_ for _ in ()).throw(ott.PlayerRejected('private-token', {})))
        code, data, err = self.run_cli(['prov', 'm3u', '1'], target='t1,t2')
        self.assertEqual((code, err), (1, ''))
        self.assertEqual((data['succeeded'], data['failed']), (1, 1))
        self.assertEqual(data['results'][1]['data']['provider'], 'm3u')
        self.assertNotIn('private-token', json.dumps(data))

    def modern_catalog(self, loading_first=False, wrong_provider=False):
        self.overrides['capabilities'] = self.capabilities(modern=True)
        categories_reads = []
        def categories(device, params):
            categories_reads.append(params)
            return {'version': 1, 'runtime': 'runtime1', 'catalog': 'source-snapshot',
                    'offset': 0, 'total': 2, 'has_more': False,
                    'loading': loading_first and len(categories_reads) == 1,
                    'op': 'list', 'provider': 'wrong' if wrong_provider else self.provider,
                    'selected': 'favorites', 'categories': [
                        {'id': 'all', 'name': 'All', 'count': 3},
                        {'id': 'favorites', 'name': 'Favorites', 'count': 1}]}
        def channels(device, params):
            self.assertGreaterEqual(len(categories_reads), 2 if loading_first else 1)
            self.assertEqual(params['category'], 'all')
            offset, limit = params['offset'], params['limit']
            self.assertEqual(params['catalog'], 'source-snapshot' if offset == 0 else 'channel-snapshot')
            return {'version': 1, 'runtime': 'runtime1', 'catalog': 'channel-snapshot',
                    'offset': offset, 'total': len(self.rows), 'has_more': offset + limit < len(self.rows),
                    'loading': False, 'category': 'all', 'channels': self.rows[offset:offset + limit]}
        self.overrides.update(categories=categories, channels=channels)
        return categories_reads

    def test_modern_catalog_reads_all_without_category_select_request(self):
        self.modern_catalog()
        code, data, err = self.run_cli(['provider', 'plex', '1'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(data['playback']['channel']['number'], 1)
        self.assertEqual(self.mutations()[-1], ('dev1', 'play_catalog', {'catalog': 'channel-snapshot', 'id': 'one'}))
        self.assertTrue(all(params['op'] == 'list' for _, action, params in self.calls if action == 'categories'))
        self.assertNotIn('epg_catalog', [action for _, action, _ in self.calls])

    def test_modern_loading_waits_before_enumerating_and_random_draw(self):
        reads = self.modern_catalog(loading_first=True)
        with mock.patch.object(ott.secrets, 'choice', side_effect=lambda rows: rows[-1]) as choice:
            code, data, err = self.run_cli(['provider', 'plex', 'R'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(len(reads), 2)
        choice.assert_not_called()
        self.assertEqual(self.mutations()[-1][2], {'catalog': 'source-snapshot', 'random': {}})
        self.assertNotIn('channels', [action for _, action, _ in self.calls])
        self.assertEqual(data['playback']['channel']['id'], 'seven')

    def test_random_refreshes_destination_capabilities_in_both_directions(self):
        self.modern_catalog()
        self.overrides['capabilities'] = lambda device, params: self.capabilities(modern=self.provider == 'plex')
        for origin, destination, expected, absent in (
                ('m3u', 'plex', 'categories', 'epg_catalog'),
                ('plex', 'm3u', 'epg_catalog', 'categories')):
            with self.subTest(origin=origin, destination=destination):
                self.provider = origin
                self.calls.clear()
                code, data, err = self.run_cli(['provider', destination, 'R'])
                self.assertEqual((code, err), (0, ''))
                actions = [action for _, action, _ in self.calls]
                self.assertEqual(actions.count('capabilities'), 2)
                self.assertLess(actions.index('capabilities'), actions.index('provider'))
                self.assertGreater(actions.index('capabilities', actions.index('provider')), actions.index('status'))
                self.assertIn(expected, actions)
                self.assertNotIn(absent, actions)
                self.assertEqual(data['playback']['channel']['id'], 'seven')

    def test_random_support_disappearing_after_switch_prevents_playback(self):
        def capabilities(device, params):
            result = self.capabilities(modern=self.provider == 'plex')
            if self.provider == 'plex':
                result.pop('random')
            return result
        self.overrides['capabilities'] = capabilities
        code, data, err = self.run_cli(['provider', 'plex', 'R'])
        self.assertEqual(code, 1)
        self.assertIn('not supported', err)
        self.assertEqual(data['playback']['stage'], 'catalog')
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])

    def test_modern_wrong_provider_never_requests_channels_or_play(self):
        self.modern_catalog(wrong_provider=True)
        code, data, _ = self.run_cli(['provider', 'plex', '1'])
        self.assertEqual(code, 1)
        self.assertEqual(data['playback']['stage'], 'catalog')
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])
        self.assertNotIn('channels', [action for _, action, _ in self.calls])

    def test_unsupported_or_invalid_bound_catalog_never_falls_back_to_plain_play(self):
        for response in (ott.PlayerUnsupported('private-token'), {'channels': self.rows},
                         {'catalog': 'snapshot', 'channels': [self.rows[0], self.rows[0]]}):
            with self.subTest(response=response):
                self.calls.clear()
                self.overrides['epg_catalog'] = response
                code, data, err = self.run_cli(['provider', 'm3u', '1'])
                self.assertEqual(code, 1)
                self.assertEqual(data['playback']['stage'], 'catalog')
                self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])
                self.assertNotIn('private-token', err)

    def test_invalid_explicit_boundary_is_rejected_before_any_rpc(self):
        for tail in (['--'], ['--', '1'], ['m3u', '--'], ['m3u', '--', '--', '1']):
            with self.subTest(tail=tail):
                self.calls.clear()
                code, _, _ = self.run_cli(['provider'] + tail)
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, [])

    def test_group_projects_untrusted_bare_provider_ack_extensions(self):
        self.overrides['provider'] = {'dispatched': True, 'provider': 'm3u',
                                      'playback': {'secret': 'private-token'}, 'wake': {'secret': 'private-token'}}
        code, data, _ = self.run_cli(['provider', 'm3u'], target='t1,t2')
        self.assertEqual(code, 0)
        self.assertNotIn('private-token', json.dumps(data))

    def test_single_chain_sanitizes_provider_preflight_and_switch_rejections(self):
        for action in ('providers', 'provider'):
            for kind in (ott.PlayerRejected, ott.PlayerUnsupported):
                with self.subTest(action=action, kind=kind):
                    self.calls.clear()
                    self.overrides = {action: kind('private-token', {})}
                    code, data, err = self.run_cli(['provider', 'm3u', '1'])
                    self.assertEqual((code, data), (1, None))
                    self.assertNotIn('private-token', err)
                    self.assertIn('The request was not repeated', err)
                    self.assertEqual([verb for _, verb, _ in self.mutations()],
                                     ['provider'] if action == 'provider' else [])

    def test_generic_catalog_preserves_valid_unicode_ids_and_rejects_oversize_lists(self):
        self.rows = [{'id': 'я' * 400, 'number': 1, 'name': 'Кино'}]
        code, data, err = self.run_cli(['provider', 'm3u', '1'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(data['playback']['channel']['id'], self.rows[0]['id'])
        self.calls.clear()
        self.overrides['epg_catalog'] = {'catalog': 'snapshot', 'channels': self.rows * 10001}
        code, data, _ = self.run_cli(['provider', 'm3u', 'R'])
        self.assertEqual(code, 1)
        self.assertEqual(data['playback']['stage'], 'catalog')
        self.assertEqual([action for _, action, _ in self.mutations()], ['provider'])

    def test_modern_loading_channel_page_never_enumerates_the_incomplete_tail(self):
        self.modern_catalog()
        channels = self.overrides['channels']
        calls = []
        def loading_once(device, params):
            calls.append(params['offset'])
            page = channels(device, params)
            page['loading'] = len(calls) == 1
            return page
        self.overrides['channels'] = loading_once
        code, _, err = self.run_cli(['provider', 'plex', '1'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(calls, [0, 0, 2])


if __name__ == '__main__':
    unittest.main()
