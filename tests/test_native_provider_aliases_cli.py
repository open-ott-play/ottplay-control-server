"""Native provider aliases preserve the player's actual brand identity."""
import copy
import json
from pathlib import Path
import unittest

import test_provider_play_cli as provider_tests


ott = provider_tests.ott


class NativeProviderAliasTest(unittest.TestCase):
    def setUp(self):
        self.fixture = provider_tests.ProviderPlayTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.providers.append({'id': 'edem', 'index': 8, 'name': 'Edem / iLook TV'})

    def test_edem_alias_with_playback_resolves_advertised_canonical_provider_once(self):
        for alias in ('ilook', 'ilooktv', 'ILOOKTV'):
            with self.subTest(alias=alias):
                self.fixture.calls.clear()
                code, data, error = self.fixture.run_cli(['provider', alias, '1'])
                self.assertEqual((code, error), (0, ''))
                self.assertEqual(data['provider'], 'edem')
                self.assertEqual(self.fixture.mutations(), [
                    ('dev1', 'provider', {'query': 'edem'}),
                    ('dev1', 'play_catalog', {'catalog': 'source-snapshot', 'id': 'one'})])

    def test_explicit_boundary_and_exact_advertised_alias_take_precedence(self):
        self.fixture.providers.append({'id': 'ilooktv', 'index': 9, 'name': 'Separate actual provider'})
        code, data, error = self.fixture.run_cli(['provider', 'ilooktv', '--', '1'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(data['provider'], 'ilooktv')
        self.assertEqual(self.fixture.mutations()[0], ('dev1', 'provider', {'query': 'ilooktv'}))

    def test_alias_never_invents_unadvertised_provider(self):
        self.fixture.providers = [row for row in self.fixture.providers if row['id'] != 'edem']
        code, _, error = self.fixture.run_cli(['provider', 'ilooktv', '1'])
        self.assertEqual(code, 1)
        self.assertIn('No provider matches', error)
        self.assertEqual(self.fixture.mutations(), [])

    def test_single_word_alias_remains_player_owned_without_preflight(self):
        code, data, error = self.fixture.run_cli(['provider', 'ilooktv'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(data['provider'], 'ilooktv')
        self.assertEqual(self.fixture.calls, [('dev1', 'provider', {'query': 'ilooktv'})])

    def test_native_dotted_and_family_brands_remain_exact(self):
        self.fixture.providers.append({'id': 'd/maxtv', 'index': 10, 'name': 'MaxTV'})
        for brand in ('d/maxtv', 'iptv-ott.ru', 'bestlist/stalker'):
            with self.subTest(brand=brand):
                self.fixture.calls.clear()
                code, data, error = self.fixture.run_cli(['provider', brand, '1'])
                self.assertEqual((code, error), (0, ''))
                self.assertEqual(data['provider'], brand)


class NativeCategoryProviderTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'contracts/categories-v1.json'
        self.page = json.loads(path.read_text())['list_result']

    def validate(self, provider):
        page = copy.deepcopy(self.page)
        page['provider'] = provider
        return ott.media_page_metadata(page, page['runtime'], 'categories', page['offset'], 200)

    def test_actual_provider_brands_are_accepted_without_collapsing_identity(self):
        for provider in ('edem', 'ottclub', 'bestlist/stalker', 'd/maxtv', 'iptv-ott.ru', 'shara.club', 'legacy_ID'):
            with self.subTest(provider=provider):
                self.validate(provider)

    def test_unsafe_provider_identifiers_remain_rejected(self):
        for provider in (None, True, 42, '', '.', '..', '../m3u', 'm3u/..', 'm3u/./other',
                         'bestlist//stalker', 'bestlist/', '/bestlist', 'private?token=x',
                         'https://private.invalid', 'bad\nname', 'a' * 65):
            with self.subTest(provider=provider), self.assertRaises(ott.Error):
                self.validate(provider)


if __name__ == '__main__':
    unittest.main()
