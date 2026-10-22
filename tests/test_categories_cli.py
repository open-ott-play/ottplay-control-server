"""Category browsing must not turn a filtered row position into a video ID."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_categories', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
CONTRACT = json.loads((ROOT / 'contracts/categories-v1.json').read_text())


def capabilities():
    return {'version': 1, 'player': {'version': '1.1.54', 'platform': 'browser', 'runtime': 'page-one'},
            'lifecycle': [], 'input': [], 'playback': [],
            'categories': {'version': 1, 'operations': ['list', 'select'], 'maxPageSize': 200}}


def category_page(rows=None, **changes):
    rows = rows if rows is not None else [
        {'id': 'all', 'name': 'All', 'count': 3},
        {'id': 'folder-42', 'name': 'Три кота', 'count': 2}]
    return dict({'version': 1, 'runtime': 'page-one', 'op': 'list', 'catalog': 'source-one',
                 'provider': 'vportal', 'selected': 'folder-42', 'offset': 0, 'total': len(rows),
                 'has_more': False, 'loading': False, 'categories': copy.deepcopy(rows)}, **changes)


def channel_page(rows=None, **changes):
    rows = rows if rows is not None else [
        {'id': 'video-72', 'number': 72, 'name': 'Три кота'},
        {'id': 'video-901', 'number': 901, 'name': 'Три кота'}]
    return dict({'version': 1, 'runtime': 'page-one', 'catalog': 'category-one', 'category': 'folder-42',
                 'offset': 0, 'total': len(rows), 'has_more': False, 'loading': False,
                 'channels': copy.deepcopy(rows)}, **changes)


def selected(category='folder-42'):
    return {'version': 1, 'runtime': 'page-one', 'op': 'select', 'catalog': 'source-one',
            'provider': 'vportal', 'selected': category, 'dispatched': True}


class CategoriesCliTest(unittest.TestCase):
    def run_cli(self, words, replies, machine=False):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = replies
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                mock.patch.object(ott.secrets, 'choice', side_effect=lambda rows: rows[-1]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['--json'] if machine else []) + ['tv'] + words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client.call.call_args_list

    def test_shared_contract_bounds_capability_and_results(self):
        self.assertEqual(CONTRACT['max_page_size'], ott.MAX_CATEGORY_PAGE_SIZE)
        self.assertEqual(CONTRACT['max_items'], ott.MAX_CATEGORY_TOTAL)
        self.assertEqual(set(CONTRACT['errors']), set(ott.CATEGORY_ERRORS))
        self.assertEqual(ott.capabilities_metadata(capabilities())['categories'], CONTRACT['capability'])
        for name, kind in (('list', 'categories'), ('channels', 'channels'), ('channels_next', 'channels')):
            request, result = CONTRACT[name + '_request']['params'], CONTRACT[name + '_result']
            self.assertEqual(ott.media_page_metadata(result, request['runtime'], kind, request['offset'], request['limit'],
                                                     request.get('catalog') if name != 'channels' else None,
                                                     request.get('category')), result)

    def test_listing_and_selection_are_explicit_and_preserve_names(self):
        for words in (['categories'], ['CATS'], ['categories', 'list'], ['cat'], ['category', 'status']):
            self.assertEqual(ott.parse_command(words), ('categories', {'op': 'list'}))
        for words in (['category', 'Три', 'кота'], ['CaT', 'Три кота'],
                      ['categories', 'select', 'Три', 'кота'], ['cats', 'set', 'Три кота']):
            self.assertEqual(ott.parse_command(words), ('categories', {'op': 'select', 'query': 'Три кота'}))
        for words in (['cat', 'all'], ['categories', 'select', 'ALL']):
            self.assertEqual(ott.parse_command(words), ('categories', {'op': 'select', 'category_id': 'all'}))
        self.assertEqual(ott.parse_command(['cat', 'ID:folder-42']),
                         ('categories', {'op': 'select', 'category_id': 'folder-42'}))
        for name in ('List', 'Status', 'Select', 'Set'):
            self.assertEqual(ott.parse_command(['category', 'select', name]),
                             ('categories', {'op': 'select', 'query': name}))

    def test_old_capabilities_remain_valid_and_new_controls_are_discoverable(self):
        old = capabilities()
        del old['categories']
        self.assertEqual(ott.capabilities_metadata(old), old)
        current = capabilities()
        self.assertEqual(ott.capabilities_metadata(current), current)
        for caps, advertised in ((old, False), (current, True)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ott.print_player_status({'capabilities': caps}, 'Living room')
            self.assertEqual("ott 'Living room' categories" in output.getvalue(), advertised)
            self.assertEqual('No controls are currently advertised' in output.getvalue(), not advertised)

    def test_malformed_capabilities_reject_private_or_inconsistent_fields(self):
        variants = [None, {}, {'version': True, 'operations': ['list'], 'maxPageSize': 200},
                    {'version': 1, 'operations': ['list'], 'maxPageSize': True},
                    {'version': 1, 'operations': ['list'], 'maxPageSize': 201},
                    {'version': 1, 'operations': ['list', 'list'], 'maxPageSize': 200},
                    {'version': 1, 'operations': ['play'], 'maxPageSize': 200},
                    {'version': 1, 'operations': ['list'], 'maxPageSize': 200, 'token': 'private'}]
        for value in variants:
            with self.subTest(value=value), self.assertRaisesRegex(ott.Error, 'invalid capabilities'):
                ott.capabilities_metadata(dict(capabilities(), categories=value))

    def test_invalid_category_syntax_never_contacts_player(self):
        for words in (['categories', 'Три кота'], ['categories', 'list', 'extra'],
                      ['cat', 'select'], ['category', ''], ['cat', 'id:'],
                      ['cat', 'bad\nname'], ['cat', '\ud800'], ['cat', 'я' * 257]):
            with self.subTest(words=words):
                code, out, _, calls = self.run_cli(words, [])
                self.assertEqual((code, out, calls), (1, '', []))

    def test_list_and_aliases_read_complete_catalog_without_mutations(self):
        for command in ('categories', 'cats', 'category', 'cat'):
            code, out, err, calls = self.run_cli([command], [capabilities(), category_page()])
            self.assertEqual((code, err), (0, ''))
            self.assertIn('* id:folder-42  Три кота (2)', out)
            self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                mock.call('dev_tv', 'categories', {'op': 'list', 'runtime': 'page-one', 'offset': 0, 'limit': 200})])

    def test_name_selection_prefers_exact_then_unique_substring_and_bound_id_without_playback(self):
        for words in (['cat', 'тРИ', 'кОТА'], ['categories', 'select', 'Три кота'], ['cat', 'id:folder-42'], ['cat', 'Три']):
            code, out, err, calls = self.run_cli(words, [capabilities(), category_page(), selected()], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', selected()))
            self.assertEqual(calls[-1], mock.call('dev_tv', 'categories', {
                'op': 'select', 'runtime': 'page-one', 'catalog': 'source-one', 'category': 'folder-42'}))
            self.assertEqual(len(calls), 3)
        code, _, err, calls = self.run_cli(['cat', 'missing'], [capabilities(), category_page()])
        self.assertEqual((code, len(calls)), (1, 2))
        self.assertIn('No category matches', err)
        page = category_page()
        page['categories'].append({'id': 'other', 'name': 'Три кота HD', 'count': 1})
        page['total'] += 1
        code, _, err, calls = self.run_cli(['cat', 'Три кота'], [capabilities(), page, selected()])
        self.assertEqual((code, err, len(calls)), (0, '', 3))
        code, _, err, calls = self.run_cli(['cat', 'Три'], [capabilities(), page])
        self.assertEqual((code, len(calls)), (1, 2))
        self.assertIn('Several categories', err)

    def test_duplicate_category_names_require_id(self):
        page = category_page()
        page['categories'].append({'id': 'folder-99', 'name': 'тРИ КОТА', 'count': 1})
        page['total'] += 1
        code, _, err, calls = self.run_cli(['cat', 'Три кота'], [capabilities(), page])
        self.assertEqual((code, len(calls)), (1, 2))
        self.assertIn('id:folder-42', err)
        self.assertIn('id:folder-99', err)
        code, _, err, calls = self.run_cli(['cat', 'id:folder-99'], [capabilities(), page, selected('folder-99')])
        self.assertEqual((code, err, len(calls)), (0, '', 3))

    def test_modern_names_match_canonical_equivalents_without_rewriting_ids(self):
        page = category_page()
        page['categories'][1]['name'] = 'Café'
        code, _, err, calls = self.run_cli(['cat', 'CAFE\u0301'], [capabilities(), page, selected()])
        self.assertEqual((code, err, len(calls)), (0, '', 3))
        rows = channel_page([{'id': 'opaque-Cafe\u0301', 'name': 'Café', 'number': 72}])
        ack = {'dispatched': True, 'channel': rows['channels'][0]}
        code, _, err, calls = self.run_cli(['Cafe\u0301'], [capabilities(), page, rows, ack])
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[-1], mock.call('dev_tv', 'play_catalog', {'catalog': 'category-one', 'id': 'opaque-Cafe\u0301'}))
        code, _, err, calls = self.run_cli(['play', 'id:opaque-Café'], [capabilities(), page, rows])
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertIn('No channels match', err)

    def test_paged_categories_pin_source_and_merge_all_rows_before_selecting(self):
        caps = capabilities(); caps['categories']['maxPageSize'] = 1
        rows = category_page()['categories']
        first = category_page(rows[:1], total=2, has_more=True)
        second = category_page(rows[1:], offset=1, total=2)
        code, _, err, calls = self.run_cli(['cat', 'Три кота'], [caps, first, second, selected()])
        self.assertEqual((code, err, len(calls)), (0, '', 4))
        self.assertEqual(calls[2], mock.call('dev_tv', 'categories', {
            'op': 'list', 'runtime': 'page-one', 'catalog': 'source-one', 'offset': 1, 'limit': 1}))
        second['catalog'] = 'changed-source'
        code, _, err, calls = self.run_cli(['cat', 'Три кота'], [caps, first, second])
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertIn('invalid media catalog', err)

    def test_incomplete_catalog_is_visible_but_name_selection_never_mutates(self):
        page = category_page(loading=True)
        code, out, err, calls = self.run_cli(['cats'], [capabilities(), page], True)
        self.assertEqual((code, len(calls)), (3, 2))
        self.assertTrue(json.loads(out)['loading'])
        self.assertIn('incomplete', err)
        code, _, err, calls = self.run_cli(['cat', 'Три кота'], [capabilities(), page])
        self.assertEqual((code, len(calls)), (1, 2))
        self.assertIn('no selection was sent', err)
        code, _, err, calls = self.run_cli(['cat', 'id:folder-42'], [capabilities(), page, selected()])
        self.assertEqual((code, err, len(calls)), (0, '', 3))

    def test_old_or_disabled_capability_does_not_send_categories(self):
        old = capabilities(); old.pop('categories')
        readonly = capabilities(); readonly['categories']['operations'] = ['list']
        for reply in (old, readonly, ott.PlayerUnsupported('unsupported')):
            code, out, err, calls = self.run_cli(['cat', 'all'], [reply])
            self.assertEqual((code, out, len(calls)), (1, '', 1))
            self.assertIn('updated player', err)

    def test_failed_partial_catalog_never_selects_or_plays(self):
        error = {'version': 1, 'runtime': 'page-one', 'op': 'list', 'state': 'error', 'error': 'catalog_incomplete'}
        for command in (['cats'], ['cat', 'all'], ['s'], ['Три кота']):
            with self.subTest(command=command):
                code, out, err, calls = self.run_cli(command, [capabilities(), ott.PlayerRejected('private-token', error)])
                self.assertEqual((code, out, len(calls)), (1, '', 2))
                self.assertIn('Catalog discovery failed', err)
                self.assertNotIn('private-token', err)

    def test_stale_select_error_is_safe_and_never_retried(self):
        error = {'version': 1, 'runtime': 'page-one', 'op': 'select', 'state': 'error', 'error': 'catalog_changed'}
        for response in (ott.PlayerRejected('private-server-token', error), ott.TransportError('Connection lost')):
            code, out, err, calls = self.run_cli(['cat', 'all'], [capabilities(), category_page(), response])
            self.assertEqual((code, out, len(calls)), (1, '', 3))
            self.assertNotIn('private-server-token', err)
        code, _, err, calls = self.run_cli(['cat', 'all'], [capabilities(), category_page(), selected()])
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertIn('may have executed', err)

    def test_total_deadline_covers_discovery_pages_and_selection_without_retries(self):
        client = mock.Mock(timeout=45)
        client.call.return_value = capabilities()
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 46]), \
                self.assertRaisesRegex(ott.Error, 'no further request'):
            ott.categories_command(client, 'dev_tv', {'op': 'list'}, 45)
        self.assertEqual((client.call.call_count, client.timeout), (1, 45))
        client.reset_mock()
        client.call.side_effect = [capabilities(), category_page(), selected('all')]
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: 46 if client.call.call_count == 3 else 0), \
                self.assertRaisesRegex(ott.Error, 'may have executed'):
            ott.categories_command(client, 'dev_tv', {'op': 'select', 'category_id': 'all'}, 45)
        self.assertEqual((client.call.call_count, client.timeout), (3, 45))

    def test_new_channel_list_uses_selected_category_and_preserves_global_numbers(self):
        code, out, err, calls = self.run_cli(['s'], [capabilities(), category_page(), channel_page()])
        self.assertEqual((code, out, err), (0, '72: Три кота\n901: Три кота\n', ''))
        self.assertEqual(calls[-1], mock.call('dev_tv', 'channels', {
            'runtime': 'page-one', 'catalog': 'source-one', 'category': 'folder-42', 'offset': 0, 'limit': 200}))
        self.assertEqual(len(calls), 3)

    def test_paged_title_search_uses_category_bound_stable_id_once(self):
        caps = capabilities(); caps['categories']['maxPageSize'] = 2
        rows = channel_page()['channels'] + [{'id': 'last', 'number': 1200, 'name': 'Три кота'}]
        first = channel_page(rows[:2], total=3, has_more=True)
        second = channel_page(rows[2:], offset=2, total=3)
        ack = {'dispatched': True, 'channel': rows[2]}
        code, out, err, calls = self.run_cli(['Три кота'], [caps, category_page(), first, second, ack], True)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['channels'], rows)
        self.assertEqual(calls[-2], mock.call('dev_tv', 'channels', {
            'runtime': 'page-one', 'catalog': 'category-one', 'category': 'folder-42',
            'offset': 2, 'limit': 2, 'search': 'Три кота'}))
        self.assertEqual(calls[-1], mock.call('dev_tv', 'play_catalog', {'catalog': 'category-one', 'id': 'last'}))

    def test_stale_catalog_during_video_play_is_never_replayed_as_number(self):
        rejection = ott.PlayerRejected('private-server-token', {'error': 'catalog_changed'})
        code, out, err, calls = self.run_cli(['Три кота'], [capabilities(), category_page(), channel_page(), rejection], True)
        self.assertEqual((code, len(calls)), (1, 4))
        self.assertEqual(calls[-1], mock.call('dev_tv', 'play_catalog', {'catalog': 'category-one', 'id': 'video-901'}))
        self.assertIn('playback', json.loads(out))
        self.assertNotIn('private-server-token', out + err)

    def test_partial_title_search_refuses_play_but_exact_loaded_video_id_is_allowed(self):
        page = channel_page(loading=True)
        code, out, err, calls = self.run_cli(['Три кота'], [capabilities(), category_page(), page], True)
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertIn('no playback was requested', err)
        self.assertTrue(json.loads(out)['loading'])
        ack = {'dispatched': True, 'channel': page['channels'][0]}
        code, out, err, calls = self.run_cli(['play', 'id:video-72'], [capabilities(), category_page(), page, ack], True)
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[-1], mock.call('dev_tv', 'play_catalog', {'catalog': 'category-one', 'id': 'video-72'}))

    def test_discovery_failures_do_not_fall_back_to_unbound_playback(self):
        disabled = capabilities(); disabled['categories']['operations'] = []
        for reply in (ott.TransportError('Lost connection'), ott.HTTPError(500), {}, {'version': True}, disabled):
            code, _, _, calls = self.run_cli(['Три кота'], [reply])
            self.assertEqual((code, calls), (1, [mock.call('dev_tv', 'capabilities', {})]))
        old = capabilities(); old.pop('categories')
        code, _, err, calls = self.run_cli(['play', 'id:video-72'], [old])
        self.assertEqual((code, len(calls)), (1, 1))
        self.assertIn('requires', err)

    def test_malformed_exact_ids_and_utf8_limits(self):
        for words in (['play', 'id:'], ['play', 'id:bad\nname'], ['play', 'id:\ud800'],
                      ['cat', 'id:' + 'я' * 513], ['play', 'id:' + 'я' * 1025]):
            code, out, _, calls = self.run_cli(words, [])
            self.assertEqual((code, out, calls), (1, '', []))
        self.assertEqual(ott.parse_command(['cat', 'id:' + 'я' * 512])[1]['category_id'], 'я' * 512)
        self.assertEqual(ott.parse_command(['cat', 'я' * 256])[1]['query'], 'я' * 256)

    def test_bad_catalog_pages_fail_before_selection_or_playback(self):
        mutations = [lambda x: x.update(runtime='other'), lambda x: x.update(catalog='bad/token'),
                     lambda x: x.update(total=True), lambda x: x.update(has_more=True),
                     lambda x: x.update(loading='false'), lambda x: x.update(private='private-token'),
                     lambda x: x['categories'][1].update(id='all'),
                     lambda x: x['categories'][1].update(name='bad\nname'),
                     lambda x: x['categories'][1].update(count=-1)]
        for mutation in mutations:
            page = category_page(); mutation(page)
            code, out, err, calls = self.run_cli(['cat', 'all'], [capabilities(), page])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('private-token', err)
        caps = capabilities(); caps['categories']['maxPageSize'] = 2
        first = channel_page(total=3, has_more=True)
        for last in (channel_page([{'id': 'video-72', 'number': 1200, 'name': 'Три кота'}], offset=2, total=3),
                     channel_page([{'id': 'new-id', 'number': 900, 'name': 'Три кота'}], offset=2, total=3),
                     channel_page([{'id': 'new-id', 'number': 1200, 'name': 'Три кота'}], offset=2, total=3, catalog='changed')):
            code, out, _, calls = self.run_cli(['Три кота'], [caps, category_page(), first, last])
            self.assertEqual((code, out, len(calls)), (1, '', 4))

    def test_filtered_listing_keeps_global_numbers_without_starting_playback(self):
        rows = [{'id': 'video-72', 'number': 72, 'name': 'Три кота'},
                {'id': 'video-901', 'number': 901, 'name': 'Три кота'}]
        for command in ('s', 'channels'):
            code, out, err, calls = self.run_cli([command, 'Три кота'], [ott.PlayerUnsupported('Legacy player'), {'channels': rows}])
            self.assertEqual((code, err, out), (0, '', '72: Три кота\n901: Три кота\n'))
            self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}), mock.call('dev_tv', 'channels', {'search': 'Три кота'})])

    def test_duplicate_video_titles_use_returned_global_number_and_stable_ack_id(self):
        rows = [{'id': 'video-72', 'number': 72, 'name': 'Три кота'},
                {'id': 'video-901', 'number': 901, 'name': 'Три кота'}]
        ack = {'dispatched': True, 'channel': copy.deepcopy(rows[1])}
        code, out, err, calls = self.run_cli(['Три кота'], [ott.PlayerUnsupported('Legacy player'), {'channels': rows}, ack], True)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['playback']['channel']['id'], 'video-901')
        self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}), mock.call('dev_tv', 'channels', {'search': 'Три кота'}),
                                mock.call('dev_tv', 'play', {'query': '901'})])
        ack['channel']['id'] = 'video-elsewhere'
        code, _, err, calls = self.run_cli(['Три кота'], [ott.PlayerUnsupported('Legacy player'), {'channels': rows}, ack])
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertIn('do not repeat', err)

    def test_profiles_queue_and_reserved_titles_keep_existing_dispatch(self):
        for words, expected in [
            (['profile', '2'], ('profile', {'number': 2})),
            (['plex', 'play', '72'], ('plex_queue', {'op': 'play', 'ids': ['72']})),
            (['next'], ('playback', {'operation': 'next_channel'})),
            (['prev'], ('playback', {'operation': 'previous_channel'})),
            (['random'], ('command', {'command': 'random_channel'})),
            (['kiosk', 'on'], ('kiosk', {'mode': 'on'})),
            (['play', 'cat'], ('play', {'query': 'cat'})),
        ]:
            self.assertEqual(ott.parse_command(words), expected)


if __name__ == '__main__':
    unittest.main()
