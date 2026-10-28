import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli'))
import global_favorites_document as codec
import preset_favorites as legacy
import preset_global_favorites as storage

PRIVATE = 'fixture-only-private-value'


def names(config):
    value = config.get('presets', {})
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
        raise ValueError(PRIVATE)
    if len({k.casefold() for k in value}) != len(value):
        raise ValueError(PRIVATE)
    return list(value)


def resolve(config, requested):
    matches = [name for name in names(config) if name.casefold() == requested.casefold()]
    if len(matches) != 1:
        raise ValueError(PRIVATE)
    return matches[0]


def row(item='001', title='Film'):
    return {'title': title,
            'origin': {'provider': 'm3u', 'profile': 'TV', 'subprofile': None, 'sourceAlias': 'Family'},
            'locator': {'kind': 'channel', 'channelId': item}}


def document(*rows):
    return {'version': 1, 'entries': list(rows)}


def old_document(*items):
    return {'fixture': list(items)}


def validate_old(value):
    if not isinstance(value, dict) or set(value) != {'fixture'} or not isinstance(value['fixture'], list):
        raise ValueError(PRIVATE)
    return value


class GlobalPresetFavoritesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ott-global-preset-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / 'cli.json'
        self.value = {'presets': {'Home': {'settings': PRIVATE}}, 'other': {'private': PRIVATE},
                      'preset_favorites': {'version': 1, 'presets': {'Home': old_document('legacy')}}}
        self.write(self.value)
        self.store = storage.Store(self.path, preset_names=names, resolve_preset=resolve)
        self.old = legacy.Store(self.path, preset_names=names, resolve_preset=resolve,
                                validate_document=validate_old)

    def write(self, value):
        raw = ('  ' + json.dumps(value, ensure_ascii=False, indent=3) + '\n\n').encode('utf-8')
        self.path.write_bytes(raw)
        self.path.chmod(0o600)
        return raw

    def current(self):
        return json.loads(self.path.read_bytes())

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(storage.StorageError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PRIVATE, str(caught.exception))
        return caught.exception

    def test_absent_global_never_imports_or_rewrites_legacy(self):
        before = self.path.read_bytes()
        snapshot = self.store.snapshot('hOmE')
        self.assertIsNone(snapshot['document'])
        self.assertEqual(snapshot['preset'], 'Home')
        self.assertEqual(snapshot['config_revision'], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.old.snapshot('Home')['document'], old_document('legacy'))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_global_apply_preserves_legacy_settings_and_exact_backup_bytes(self):
        self.value[storage.KEY] = {'version': 1, 'presets': {'Removed': document(row('002'))}}
        before = self.write(self.value)
        plan = self.store.plan('home', document(row()))
        self.assertEqual(plan.storage_key, storage.KEY)
        result = self.store.apply(plan)
        after = self.current()
        self.assertEqual(after[storage.KEY]['presets']['Home'], document(row()))
        self.assertEqual(after[storage.KEY]['presets']['Removed'], document(row('002')))
        after.pop(storage.KEY)
        expected = copy.deepcopy(self.value)
        expected.pop(storage.KEY)
        self.assertEqual(after, expected)
        self.assertEqual(Path(result['backup_path']).read_bytes(), before)
        self.assertEqual(result['config_revision'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(self.store.snapshot('Home')['orphans'], ['Removed'])
        if os.name != 'nt':
            for path in self.directory.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_each_store_ignores_other_key_schema_but_validates_its_own(self):
        self.value[storage.KEY] = {'version': 999, 'private': PRIVATE}
        self.write(self.value)
        self.assertEqual(self.old.snapshot('Home')['document'], old_document('legacy'))
        self.old.apply(self.old.plan('Home', old_document('updated')))
        self.assertEqual(self.current()[storage.KEY], self.value[storage.KEY])
        self.assert_error('invalid_extension', self.store.snapshot, 'Home')
        self.value['preset_favorites'] = {'malformed': PRIVATE}
        self.value[storage.KEY] = {'version': 1, 'presets': {'Home': document(row())}}
        self.write(self.value)
        self.assertEqual(self.store.snapshot('Home')['document'], document(row()))
        self.store.apply(self.store.plan('Home', document(row('002'))))
        self.assertEqual(self.current()['preset_favorites'], {'malformed': PRIVATE})
        self.assert_error('invalid_extension', self.old.snapshot, 'Home')

    def test_no_format_guessing_and_invalid_callback_is_redacted(self):
        raw = self.path.read_bytes()
        self.assert_error('invalid_document', self.store.plan, 'Home', old_document('legacy'))
        self.assert_error('invalid_document', self.store.plan, 'Home', {'version': 1, 'scopes': []})
        self.assert_error('invalid_document', self.old.plan, 'Home', document())
        def fail(_):
            raise ValueError(PRIVATE)
        injected = storage.Store(self.path, preset_names=names, resolve_preset=resolve, validate_document=fail)
        self.assert_error('invalid_document', injected.plan, 'Home', document())
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_empty_global_is_present_and_noop_has_no_io_side_effects(self):
        self.value[storage.KEY] = {'version': 1, 'presets': {'Home': document()}}
        before = self.write(self.value)
        ino = self.path.stat().st_ino
        self.assertEqual(self.store.get(self.value, 'home'), document())
        plan = self.store.plan('Home', document())
        with mock.patch.object(legacy._Lock, '__enter__', side_effect=AssertionError('no lock')):
            result = self.store.apply(plan)
        self.assertFalse(result['changed'])
        self.assertIsNone(result['backup_path'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_ino, ino)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_extension_names_require_exact_existing_canonical_spelling(self):
        for invalid in (None, [], {'version': True, 'presets': {}}, {'version': 1, 'profiles': {}}):
            self.value[storage.KEY] = invalid
            self.write(self.value)
            self.assert_error('invalid_extension', self.store.snapshot, 'Home')
        self.value[storage.KEY] = {'version': 1, 'presets': {'home': document()}}
        self.write(self.value)
        self.assert_error('noncanonical_name', self.store.snapshot, 'Home')
        self.value[storage.KEY]['presets']['Home'] = document()
        self.write(self.value)
        self.assert_error('invalid_presets', self.store.snapshot, 'Home')

    def test_stale_config_revision_blocks_even_unrelated_edits(self):
        snapshot = self.store.snapshot('Home')
        plan = self.store.plan('Home', document(row()), expected_revision=snapshot['config_revision'])
        self.value['other']['new'] = 'concurrent change'
        changed = self.write(self.value)
        self.assert_error('config_changed', self.store.apply, plan)
        self.assert_error('config_changed', self.store.plan, 'Home', document(row()),
                          expected_revision=snapshot['config_revision'])
        self.assertEqual(self.path.read_bytes(), changed)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_old_and_global_updates_conflict_then_fresh_plan_preserves_both(self):
        global_plan = self.store.plan('Home', document(row()))
        self.old.apply(self.old.plan('Home', old_document('new legacy')))
        self.assert_error('config_changed', self.store.apply, global_plan)
        self.store.apply(self.store.plan('Home', document(row())))
        self.assertEqual(self.old.snapshot('Home')['document'], old_document('new legacy'))
        self.assertEqual(self.store.snapshot('Home')['document'], document(row()))
        self.assertEqual(self.current()['other'], self.value['other'])

    def test_plans_are_key_bound_even_with_identical_injected_document_schema(self):
        other = legacy.Store(self.path, preset_names=names, resolve_preset=resolve,
                             validate_document=self.store.validate_document)
        self.value.pop('preset_favorites')
        raw = self.write(self.value)
        new_plan = self.store.plan('Home', document())
        old_plan = other.plan('Home', document())
        self.assertEqual(old_plan.storage_key, legacy.KEY)
        self.assert_error('invalid_plan', other.apply, new_plan)
        self.assert_error('invalid_plan', self.store.apply, old_plan)
        self.assert_error('invalid_plan', self.store.apply, replace(new_plan, storage_key='presets'))
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_explicit_merge_then_replace_uses_codec_order_and_titles(self):
        self.store.apply(self.store.plan('Home', document(row('001', 'First'))))
        before = self.store.snapshot('Home')
        merged = codec.plan_documents(before['document'],
                                      [document(row('001', 'First'), row('002', 'Second'))], 'merge')
        self.assertTrue(merged['can_commit'])
        self.store.apply(self.store.plan('Home', merged['document'], expected_revision=before['config_revision']))
        self.assertEqual(self.store.snapshot('Home')['document'], document(row('001', 'First'), row('002', 'Second')))
        before = self.store.snapshot('Home')
        replaced = codec.plan_documents(before['document'], [document(row('002', 'Deliberately renamed'))], 'replace')
        self.assertTrue(replaced['can_commit'])
        self.store.apply(self.store.plan('Home', replaced['document'], expected_revision=before['config_revision']))
        self.assertEqual(self.store.snapshot('Home')['document'], document(row('002', 'Deliberately renamed')))
        self.assertEqual(self.old.snapshot('Home')['document'], old_document('legacy'))

    def test_conflicted_preview_is_explicit_and_does_not_touch_storage(self):
        self.value[storage.KEY] = {'version': 1, 'presets': {'Home': document(row('001', 'Original'))}}
        raw = self.write(self.value)
        preview = codec.plan_documents(self.store.snapshot('Home')['document'],
                                       [document(row('001', 'Different title'))], 'merge')
        self.assertFalse(preview['can_commit'])
        self.assertEqual(len(preview['title_conflicts']), 1)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_legacy_positional_plan_default_and_summary_stay_compatible(self):
        original = self.old.plan('Home', old_document('updated'))
        positional = legacy.UpdatePlan(original.path, original.preset, original.source_revision,
                                       original.before_revision, original.after_revision, original.changed,
                                       original._document)
        self.assertEqual(positional, original)
        self.assertEqual(set(positional.summary()),
                         {'preset', 'source_revision', 'before_revision', 'after_revision', 'changed'})
        self.old.apply(positional)
        self.assertEqual(self.old.snapshot('Home')['document'], old_document('updated'))
        self.assertNotIn(storage.KEY, self.current())

    def test_mutated_document_cannot_change_planned_digest(self):
        incoming = document(row())
        plan = self.store.plan('Home', incoming)
        incoming['entries'][0]['title'] = 'caller mutation'
        self.assertEqual(plan._document['entries'][0]['title'], 'Film')
        plan._document['entries'][0]['title'] = 'changed plan'
        self.assert_error('invalid_plan', self.store.apply, plan)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_backup_restores_only_selected_global_document(self):
        self.value[storage.KEY] = {'version': 1, 'presets': {'Home': document(row('001'))}}
        raw = self.write(self.value)
        result = self.store.apply(self.store.plan('Home', document(row('002'))))
        current = self.current()
        current['other']['new'] = 'keep current'
        current['preset_favorites']['presets']['Home'] = old_document('keep current legacy')
        current[storage.KEY]['presets']['Orphan'] = document(row('003'))
        self.write(current)
        snapshot = self.store.snapshot('Home')
        restored = self.store.backup_document('home', result['backup_path'])
        self.assertEqual(restored['backup_revision'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(restored['document'], document(row('001')))
        self.store.apply(self.store.plan('Home', restored['document'], expected_revision=snapshot['config_revision']))
        current[storage.KEY]['presets']['Home'] = document(row('001'))
        self.assertEqual(self.current(), current)
        self.assertEqual(self.old.backup_document('Home', result['backup_path'])['document'], old_document('legacy'))

    def test_backup_without_global_document_does_not_import_legacy(self):
        result = self.store.apply(self.store.plan('Home', document(row())))
        self.assert_error('backup_document_missing', self.store.backup_document, 'Home', result['backup_path'])
        self.assertEqual(self.old.backup_document('Home', result['backup_path'])['document'], old_document('legacy'))
        backup = Path(result['backup_path'])
        backup.write_bytes(backup.read_bytes() + b' ')
        self.assert_error('invalid_backup', self.store.backup_document, 'Home', backup)

    @unittest.skipIf(os.name == 'nt', 'POSIX same-lock and private-leaf assertions')
    def test_shared_lock_and_unsafe_files_block_before_backup(self):
        plan = self.store.plan('Home', document(row()))
        with legacy._Parent(self.directory) as parent, legacy._Lock(parent, self.path.name + '.preset-favorites.lock'):
            self.assert_error('lock_unavailable', self.store.apply, plan)
            self.assert_error('lock_unavailable', self.old.apply, self.old.plan('Home', old_document('new')))
        self.assertEqual(list(self.directory.glob('*.favorites-backup-*')), [])
        self.path.chmod(0o644)
        self.assert_error('unsafe_path', self.store.snapshot, 'Home')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
        self.path.chmod(0o600)
        real = self.directory / 'real.json'
        self.path.rename(real)
        self.path.symlink_to(real)
        self.assert_error('read_failed', self.store.snapshot, 'Home')

    def test_observed_concurrent_write_is_not_overwritten(self):
        plan = self.store.plan('Home', document(row()))
        before = self.path.read_bytes()
        edited = before + b' '
        write = legacy._Parent.write_new
        def stage_then_edit(parent, name, raw):
            write(parent, name, raw)
            if name.startswith('.ott-favorites-'):
                self.path.write_bytes(edited)
        with mock.patch.object(legacy._Parent, 'write_new', stage_then_edit):
            error = self.assert_error('config_changed', self.store.apply, plan)
        self.assertEqual(self.path.read_bytes(), edited)
        self.assertEqual(Path(error.backup_path).read_bytes(), before)
        self.assertEqual(list(self.directory.glob('.ott-favorites-*')), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX directory sync')
    def test_post_replace_failure_is_uncertain_and_cannot_replay(self):
        plan = self.store.plan('Home', document(row()))
        before = self.path.read_bytes()
        sync = legacy._Parent.sync
        calls = []
        def fail_final(parent):
            calls.append(True)
            if len(calls) == 2:
                raise OSError(PRIVATE)
            return sync(parent)
        with mock.patch.object(legacy._Parent, 'sync', fail_final):
            error = self.assert_error('commit_uncertain', self.store.apply, plan)
        self.assertTrue(error.replacement_occurred)
        self.assertEqual(Path(error.backup_path).read_bytes(), before)
        self.assertEqual(self.store.snapshot('Home')['document'], document(row()))
        self.assert_error('config_changed', self.store.apply, plan)
        self.assertEqual(self.old.snapshot('Home')['document'], old_document('legacy'))


if __name__ == '__main__':
    unittest.main()
