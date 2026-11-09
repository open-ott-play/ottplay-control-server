"""Native Edem portable origins retain their live/portal distinction."""
import copy
import json
from pathlib import Path
import unittest

import test_global_favorites_document as document_tests
import test_global_favorites_items as items_tests

codec = document_tests.codec
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'contracts/global-favorites-edem.fixtures.json').read_text())
SCHEMA = json.loads((ROOT / 'docs/design/favorites-global-items-v1.schema.json').read_text())


class EdemFavoritesTest(unittest.TestCase):
    def test_schema_keeps_exact_provider_and_closed_locator_boundaries(self):
        guards = SCHEMA['$defs']['portable_entry']['allOf']
        pairs = [(guard['if']['properties']['origin']['properties']['provider'],
                  guard['then']['properties']['locator']['properties']['kind']) for guard in guards]
        self.assertEqual(pairs, [
            ({'const': 'plex'}, {'const': 'plex-media'}),
            ({'const': 'local'}, {'const': 'local-media'}),
            ({'const': 'vportal'}, {'const': 'vportal-media'}),
            ({'not': {'enum': ['plex', 'local', 'vportal', 'm3u', 'edem']}},
             {'enum': ['channel', 'channel-name']}),
            ({'enum': ['m3u', 'edem']}, {'enum': ['channel', 'channel-name', 'vportal-media']}),
        ])
        portal = SCHEMA['$defs']['vportal']
        self.assertIs(portal['additionalProperties'], False)
        self.assertEqual(set(portal['required']), {'kind', 'idField', 'id'})
        self.assertEqual(set(portal['properties']), {'kind', 'idField', 'id'})

    def test_shared_native_entries_roundtrip_without_identity_changes(self):
        for entry in FIXTURE.values():
            with self.subTest(locator=entry['locator']['kind'], subprofile=entry['origin']['subprofile']):
                document = {'version': 1, 'entries': [entry]}
                self.assertEqual(codec.validate_document(document), document)
                self.assertEqual(codec.load_document(codec.canonical_bytes(document)), document)
                self.assertEqual(json.loads(codec.identity(entry)),
                                 {'origin': entry['origin'], 'locator': entry['locator']})

    def test_portal_list_and_current_responses_accept_original_metadata(self):
        for entry in FIXTURE.values():
            for operation in ('list', 'current'):
                with self.subTest(operation=operation, entry=entry['title']):
                    response = copy.deepcopy(items_tests.FIX[operation + '_result'])
                    if operation == 'list':
                        response['entries'] = [dict(response['entries'][0], entry=copy.deepcopy(entry))]
                        response['total'] = 1
                        response['next_cursor'] = None
                    else:
                        response['entry'] = copy.deepcopy(entry)
                    checked = items_tests.checked(items_tests.FIX[operation + '_request'], response)
                    self.assertEqual(checked.data, response)
                    self.assertFalse(checked.verified)

    def test_subprofile_and_provider_identities_are_not_normalized(self):
        entry = FIXTURE['portal_entry']
        original = codec.identity(entry)
        for subprofile in ('Movies/Comedy', 'movies / comedy', 'Playlist 3', None):
            changed = copy.deepcopy(entry)
            changed['origin']['subprofile'] = subprofile
            self.assertNotEqual(codec.identity(changed), original)
        merged = codec.plan_documents({'version': 1, 'entries': [FIXTURE['live_entry']]},
                                     [{'version': 1, 'entries': [entry]}], 'merge')
        self.assertTrue(merged['can_commit'])
        self.assertEqual(merged['document']['entries'], [FIXTURE['live_entry'], entry])

    def test_edem_aliases_and_unrelated_providers_do_not_gain_portal_locators(self):
        for provider in ('ilook', 'ilooktv', 'Edem', 'edem/portal', 'operator', 'stalker',
                         'bestlist/stalker', 'plex', 'local'):
            with self.subTest(provider=provider):
                entry = copy.deepcopy(FIXTURE['portal_entry'])
                entry['origin']['provider'] = provider
                with self.assertRaises(codec.DocumentError):
                    codec.validate_document({'version': 1, 'entries': [entry]})

    def test_portal_locator_stays_closed_and_credential_free(self):
        for locator in (
                {'kind': 'vportal-media', 'idField': 'id', 'id': '47', 'path': 'Movies/Comedy'},
                {'kind': 'vportal-media', 'idField': 'url', 'id': '47'},
                {'kind': 'vportal-media', 'idField': 'id', 'id': 'https://fixture-secret.invalid'},
                {'kind': 'vportal-media', 'idField': 'id', 'id': 'id?token=fixture-secret'},
                {'kind': 'vportal-media', 'idField': 'id', 'id': True},
                {'kind': 'vportal-media', 'idField': 'id', 'id': 'a' * 257},
                {'kind': 'plex-media', 'serverId': 'PMS-1', 'ratingKey': '47'},
                {'kind': 'local-media', 'rootAlias': 'Movies', 'relativePath': 'film.mp4'}):
            entry = copy.deepcopy(FIXTURE['portal_entry'])
            entry['locator'] = locator
            with self.subTest(locator=locator), self.assertRaises(codec.DocumentError) as caught:
                codec.validate_document({'version': 1, 'entries': [entry]})
            self.assertNotIn('fixture-secret', str(caught.exception))
        for field in codec.VPORTAL_ID_FIELDS:
            entry = copy.deepcopy(FIXTURE['portal_entry'])
            entry['locator']['idField'] = field
            self.assertEqual(codec.validate_document({'version': 1, 'entries': [entry]})['entries'][0], entry)


if __name__ == '__main__':
    unittest.main()
