import copy
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


storage = load('ott_preset_favorites_tested', ROOT / 'cli/preset_favorites.py')
ott = load('ott_preset_favorites_cli', ROOT / 'cli/ott.py')
PRIVATE = 'fixture-only-private-configuration-value'


def document(*items):
    # The callback, not storage, owns this deliberately synthetic test schema.
    return {'fixture': list(items)}


def validate_document(value):
    if (not isinstance(value, dict) or set(value) != {'fixture'}
            or not isinstance(value['fixture'], list)
            or any(not isinstance(item, str) for item in value['fixture'])):
        raise ValueError(PRIVATE)
    return value


def config():
    return {
        'server': 'https://controller.example.invalid',
        'server_config': PRIVATE,
        'players': {'test-player': 'fixture-device'},
        'unrelated': {'nested': [1, True, PRIVATE]},
        'presets': {'Home': {
            'm3u': [{'number': 1, 'name': 'Fixture', 'playlist': 'https://playlist.example.invalid/a', 'history_hours': 0}],
            'plex': {'server': 'https://plex.example.invalid', 'token': PRIVATE},
            'active_profile': 1,
        }},
    }


class PresetFavoritesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ott-preset-favorites-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / 'cli.json'
        self.value = config()
        self.write(self.value)
        self.store = self.make_store(self.path)

    def make_store(self, path):
        return storage.Store(path, preset_names=ott.preset_names,
                             resolve_preset=lambda value, name: ott.validate_preset(value, name)[0],
                             validate_document=validate_document)

    def write(self, value):
        raw = ('  ' + json.dumps(value, ensure_ascii=False, indent=3) + '\n\n').encode('utf-8')
        self.path.write_bytes(raw)
        self.path.chmod(0o600)
        return raw

    def current(self):
        return json.loads(self.path.read_bytes())

    def backups(self):
        return list(self.directory.glob('cli.json.favorites-backup-*'))

    def fixture_backup(self, value):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        path = self.directory / ('cli.json.favorites-backup-' + digest[:12] + '-0123456789abcdef.json')
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(storage.StorageError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PRIVATE, str(caught.exception))
        return caught.exception

    def test_missing_is_no_sync_and_snapshot_is_private(self):
        before = self.path.read_bytes()
        self.assertEqual(self.store.inspect(self.value), {'present': False, 'presets': {}, 'orphans': []})
        self.assertIsNone(self.store.get(self.value, 'hOmE'))
        snapshot = self.store.snapshot('home')
        self.assertEqual(snapshot, {'preset': 'Home', 'document': None,
                                   'config_revision': hashlib.sha256(before).hexdigest(), 'orphans': []})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_exact_outer_schema_and_existing_name_validation(self):
        invalid = [None, [], {}, {'version': True, 'presets': {}}, {'version': 2, 'presets': {}},
                   {'version': 1, 'profiles': {}}, {'version': 1, 'presets': []},
                   {'version': 1, 'presets': {}, 'extra': PRIVATE}]
        for extension in invalid:
            with self.subTest(extension=extension):
                self.assert_error('invalid_extension', self.store.inspect, dict(self.value, preset_favorites=extension))
        self.assert_error('invalid_preset', self.store.get, self.value, 'missing')
        bad = copy.deepcopy(self.value)
        bad['presets']['home'] = copy.deepcopy(bad['presets']['Home'])
        self.assert_error('invalid_presets', self.store.inspect, bad)

    def test_duplicate_case_names_and_noncanonical_keys_are_rejected(self):
        for entries in ({'Home': document(), 'home': document()}, {'Gone': document(), 'gone': document()}):
            self.assert_error('invalid_presets', self.store.inspect,
                              dict(self.value, preset_favorites={'version': 1, 'presets': entries}))
        self.assert_error('noncanonical_name', self.store.inspect,
                          dict(self.value, preset_favorites={'version': 1, 'presets': {'home': document()}}))

    def test_orphans_and_unrelated_fields_are_preserved(self):
        self.value['preset_favorites'] = {'version': 1, 'presets': {'Removed': document('old'), 'Home': document('before')}}
        raw = self.write(self.value)
        self.assertEqual(self.store.snapshot('HOME')['orphans'], ['Removed'])
        receipt = self.store.apply(self.store.plan('home', document('after')))
        result = self.current()
        self.assertEqual(result['preset_favorites']['presets']['Removed'], document('old'))
        self.assertEqual(result['preset_favorites']['presets']['Home'], document('after'))
        result.pop('preset_favorites')
        original = copy.deepcopy(self.value)
        original.pop('preset_favorites')
        self.assertEqual(result, original)
        self.assertEqual(Path(receipt['backup_path']).read_bytes(), raw)

    def test_schema_is_injected_and_callback_cannot_mutate_callers(self):
        bad = {'not_the_fixture': PRIVATE}
        self.assert_error('invalid_document', self.store.plan, 'Home', bad)
        self.assertEqual(bad, {'not_the_fixture': PRIVATE})
        def other_schema(value):
            value['normalized'] = True
            return value
        self.store.validate_document = other_schema
        value = {'different_portable_schema': {'opaque': 'reference'}}
        plan = self.store.plan('Home', value)
        self.assertNotIn('normalized', value)
        self.assertTrue(plan._document['normalized'])
        value['different_portable_schema']['opaque'] = 'caller changed'
        self.assertEqual(plan._document['different_portable_schema']['opaque'], 'reference')
        for invalid in ({'value': float('nan')}, {'value': {1, 2}}, {'value': '\ud800'}):
            self.assert_error('invalid_document', self.store.plan, 'Home', invalid)

    def test_apply_backs_up_exact_bytes_and_uses_private_modes(self):
        raw = self.path.read_bytes()
        plan = self.store.plan('HOME', document('movie'))
        self.assertEqual(plan.preset, 'Home')
        self.assertNotIn(PRIVATE, repr(plan))
        self.assertNotIn('movie', repr(plan))
        self.assertEqual(set(plan.summary()), {'preset', 'source_revision', 'before_revision', 'after_revision', 'changed'})
        self.assertNotIn(PRIVATE, json.dumps(plan.summary()))
        receipt = self.store.apply(plan)
        self.assertTrue(receipt['changed'])
        self.assertEqual(Path(receipt['backup_path']).read_bytes(), raw)
        self.assertEqual(receipt['config_revision'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(self.current()['preset_favorites'], {'version': 1, 'presets': {'Home': document('movie')}})
        if os.name != 'nt':
            for path in (self.path, Path(receipt['backup_path']), self.path.with_name(self.path.name + '.preset-favorites.lock')):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)

    def test_noop_does_not_create_lock_backup_or_rewrite(self):
        self.value['preset_favorites'] = {'version': 1, 'presets': {'Home': document('movie')}}
        raw = self.write(self.value)
        identity = self.path.stat()
        plan = self.store.plan('Home', document('movie'))
        self.assertFalse(plan.changed)
        with mock.patch.object(storage.os, 'replace', side_effect=AssertionError('must not write')):
            receipt = self.store.apply(plan)
        self.assertEqual(receipt['backup_path'], None)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.path.stat().st_ino, identity.st_ino)
        self.assertEqual(self.path.stat().st_mtime_ns, identity.st_mtime_ns)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_repeated_fresh_plan_is_idempotent(self):
        self.store.apply(self.store.plan('Home', document('movie')))
        raw, paths = self.path.read_bytes(), set(self.directory.iterdir())
        self.assertFalse(self.store.apply(self.store.plan('Home', document('movie')))['changed'])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(set(self.directory.iterdir()), paths)

    def test_restore_only_favorites_from_actual_backup_preserves_current_settings(self):
        self.value['preset_favorites'] = {'version': 1, 'presets': {'Home': document('original')}}
        original = self.write(self.value)
        receipt = self.store.apply(self.store.plan('Home', document('changed')))
        current = self.current()
        current['server'] = 'https://new-controller.example.invalid'
        current['presets']['Home']['plex']['token'] = 'current-fixture-token'
        current['preset_favorites']['presets']['Orphan'] = document('retained')
        self.write(current)
        snapshot = self.store.snapshot('HOME')
        backup = self.store.backup_document('hOmE', receipt['backup_path'])
        self.assertEqual(backup, {'preset': 'Home', 'document': document('original'),
                                  'backup_revision': hashlib.sha256(original).hexdigest()})
        self.assertNotIn(PRIVATE, repr(backup))
        plan = self.store.plan('Home', backup['document'], expected_revision=snapshot['config_revision'])
        result = self.store.apply(plan)
        expected = copy.deepcopy(current)
        expected['preset_favorites']['presets']['Home'] = document('original')
        self.assertEqual(self.current(), expected)
        self.assertEqual(json.loads(Path(result['backup_path']).read_bytes()), current)

    def test_backup_preview_and_noop_make_no_files_or_writes(self):
        self.value['preset_favorites'] = {'version': 1, 'presets': {'Home': document('same')}}
        raw = self.write(self.value)
        backup_path = self.fixture_backup(self.value)
        paths, metadata = set(self.directory.iterdir()), self.path.stat()
        with mock.patch.object(storage._Lock, '__enter__', side_effect=AssertionError('must not lock')):
            snapshot = self.store.snapshot('Home')
            restored = self.store.backup_document('Home', backup_path)
            plan = self.store.plan('Home', restored['document'], expected_revision=snapshot['config_revision'])
            self.assertFalse(self.store.apply(plan)['changed'])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.path.stat().st_mtime_ns, metadata.st_mtime_ns)
        self.assertEqual(set(self.directory.iterdir()), paths)

    def test_restore_planning_rejects_concurrent_current_config_change(self):
        old = dict(self.value, preset_favorites={'version': 1, 'presets': {'Home': document('old')}})
        backup_path = self.fixture_backup(old)
        snapshot = self.store.snapshot('Home')
        restored = self.store.backup_document('Home', backup_path)
        self.value['unrelated']['new'] = 'concurrent edit'
        raw = self.write(self.value)
        self.assert_error('config_changed', self.store.plan, 'Home', restored['document'],
                          expected_revision=snapshot['config_revision'])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.backups(), [backup_path])

    def test_backup_needs_exact_selected_companion_and_current_existing_preset(self):
        cases = (self.value, dict(self.value, preset_favorites={'version': 1, 'presets': {}}),
                 dict(self.value, preset_favorites={'version': 1, 'presets': {'home': document()}}))
        for value in cases:
            with self.subTest(value=value.get('preset_favorites')):
                path = self.fixture_backup(value)
                self.assert_error('backup_document_missing', self.store.backup_document, 'Home', path)
        self.assert_error('invalid_preset', self.store.backup_document, 'Removed', self.directory / 'absent')

    def test_backup_does_not_validate_old_provider_settings_or_unselected_documents(self):
        value = {'presets': PRIVATE, 'old_provider_credentials': PRIVATE,
                 'preset_favorites': {'version': 1, 'presets': {'Home': document('old'), 'Orphan': PRIVATE}}}
        path = self.fixture_backup(value)
        result = self.store.backup_document('Home', path)
        self.assertEqual(result['document'], document('old'))
        self.assertEqual(set(result), {'preset', 'document', 'backup_revision'})
        self.assertNotIn(PRIVATE, repr(result))
        value['preset_favorites']['presets']['home'] = document('ambiguous')
        self.assert_error('invalid_presets', self.store.backup_document, 'Home', self.fixture_backup(value))

    def test_backup_family_parent_and_digest_are_checked(self):
        value = dict(self.value, preset_favorites={'version': 1, 'presets': {'Home': document('old')}})
        path = self.fixture_backup(value)
        other_name = path.with_name(path.name.replace('cli.json.', 'other.json.', 1))
        self.assert_error('invalid_backup', self.store.backup_document, 'Home', other_name)
        self.assert_error('invalid_backup', self.store.backup_document, 'Home', path.parent / 'other' / path.name)
        path.write_bytes(path.read_bytes() + b' ')
        self.assert_error('invalid_backup', self.store.backup_document, 'Home', path)

    def test_backup_selected_schema_and_size_are_bounded(self):
        invalid = dict(self.value, preset_favorites={'version': 1, 'presets': {'Home': PRIVATE}})
        self.assert_error('invalid_document', self.store.backup_document, 'Home', self.fixture_backup(invalid))
        oversized = dict(self.value, padding='x' * (len(self.path.read_bytes()) + 100))
        path = self.fixture_backup(oversized)
        with mock.patch.object(storage, 'MAX_CONFIG_BYTES', len(self.path.read_bytes()) + 1):
            self.assert_error('too_large', self.store.backup_document, 'Home', path)

    @unittest.skipIf(os.name == 'nt', 'POSIX private files, symlink and FIFO checks')
    def test_backup_rejects_public_symlink_hardlink_and_fifo_without_changes(self):
        value = dict(self.value, preset_favorites={'version': 1, 'presets': {'Home': document('old')}})
        path = self.fixture_backup(value)
        raw = self.path.read_bytes()
        path.chmod(0o644)
        self.assert_error('unsafe_path', self.store.backup_document, 'Home', path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        path.unlink()
        path.symlink_to(self.path)
        self.assert_error('backup_read_failed', self.store.backup_document, 'Home', path)
        path.unlink()
        os.link(self.path, path)
        # The current configuration is now unsafe as well: neither leaf is read.
        self.assert_error('unsafe_path', self.store.backup_document, 'Home', path)
        path.unlink()
        os.mkfifo(path, 0o600)
        self.assert_error('unsafe_path', self.store.backup_document, 'Home', path)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_snapshot_revision_rejects_edit_before_plan(self):
        snapshot = self.store.snapshot('Home')
        self.value['unrelated']['edit'] = 'concurrent'
        raw = self.write(self.value)
        self.assert_error('config_changed', self.store.plan, 'Home', document('movie'),
                          expected_revision=snapshot['config_revision'])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.backups(), [])

    def test_apply_rejects_changed_bytes_including_whitespace(self):
        plan = self.store.plan('Home', document('movie'))
        raw = self.path.read_bytes() + b' '
        self.path.write_bytes(raw)
        self.assert_error('config_changed', self.store.apply, plan)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_apply_rereads_after_acquiring_lock(self):
        plan = self.store.plan('Home', document('movie'))
        original_enter = storage._Lock.__enter__
        raw = self.path.read_bytes() + b' '
        def edit_after_lock(lock):
            result = original_enter(lock)
            self.path.write_bytes(raw)
            return result
        with mock.patch.object(storage._Lock, '__enter__', edit_after_lock):
            self.assert_error('config_changed', self.store.apply, plan)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.backups(), [])

    def test_observed_editor_change_during_staging_is_not_overwritten(self):
        plan = self.store.plan('Home', document('movie'))
        initial = self.path.read_bytes()
        edited = initial + b' '
        original = storage._Parent.write_new
        def write_and_edit(parent, name, raw):
            result = original(parent, name, raw)
            if name.startswith('.ott-favorites-'):
                self.path.write_bytes(edited)
            return result
        with mock.patch.object(storage._Parent, 'write_new', write_and_edit):
            error = self.assert_error('config_changed', self.store.apply, plan)
        self.assertEqual(self.path.read_bytes(), edited)
        self.assertEqual(Path(error.backup_path).read_bytes(), initial)
        self.assertEqual(list(self.directory.glob('.ott-favorites-*')), [])

    def test_mutated_or_cross_config_plan_is_rejected(self):
        plan = self.store.plan('Home', document('movie'))
        plan._document['fixture'].append('changed')
        self.assert_error('invalid_plan', self.store.apply, plan)
        self.assert_error('invalid_plan', self.store.apply, replace(plan, path=str(self.path.with_name('other.json'))))
        self.assertEqual(self.backups(), [])

    def test_backup_fsync_failure_keeps_original_and_removes_partial_backup(self):
        plan, raw = self.store.plan('Home', document('movie')), self.path.read_bytes()
        with mock.patch.object(storage.os, 'fsync', side_effect=OSError(PRIVATE)):
            error = self.assert_error('backup_failed', self.store.apply, plan)
        self.assertFalse(error.replacement_occurred)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.backups(), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX parent fsync')
    def test_backup_parent_fsync_failure_prevents_config_write(self):
        plan, raw = self.store.plan('Home', document('movie')), self.path.read_bytes()
        real_sync = storage.os.fsync
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(PRIVATE)
            return real_sync(fd)
        with mock.patch.object(storage.os, 'fsync', side_effect=sync):
            self.assert_error('backup_failed', self.store.apply, plan)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.backups()[0].read_bytes(), raw)
        self.assertEqual(list(self.directory.glob('.ott-favorites-*')), [])

    def test_staging_fsync_failure_keeps_original_and_durable_backup(self):
        plan, raw = self.store.plan('Home', document('movie')), self.path.read_bytes()
        real_sync, file_syncs = storage.os.fsync, []
        def sync(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                file_syncs.append(fd)
                if len(file_syncs) == 2:
                    raise OSError(PRIVATE)
            return real_sync(fd)
        with mock.patch.object(storage.os, 'fsync', side_effect=sync):
            error = self.assert_error('write_failed', self.store.apply, plan)
        self.assertFalse(error.replacement_occurred)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(Path(error.backup_path).read_bytes(), raw)
        self.assertEqual(list(self.directory.glob('.ott-favorites-*')), [])

    def test_atomic_replace_failure_keeps_original_and_backup(self):
        plan, raw = self.store.plan('Home', document('movie')), self.path.read_bytes()
        with mock.patch.object(storage.os, 'replace', side_effect=OSError(PRIVATE)):
            error = self.assert_error('write_failed', self.store.apply, plan)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(Path(error.backup_path).read_bytes(), raw)
        self.assertEqual(list(self.directory.glob('.ott-favorites-*')), [])

    def test_temporary_cleanup_failure_does_not_mask_write_failure(self):
        plan, raw = self.store.plan('Home', document('movie')), self.path.read_bytes()
        with mock.patch.object(storage.os, 'replace', side_effect=OSError(PRIVATE)), \
                mock.patch.object(storage._Parent, 'unlink', side_effect=OSError(PRIVATE)):
            error = self.assert_error('write_failed', self.store.apply, plan)
        self.assertFalse(error.replacement_occurred)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(Path(error.backup_path).read_bytes(), raw)
        leftovers = list(self.directory.glob('.ott-favorites-*'))
        self.assertEqual(len(leftovers), 1)
        if os.name != 'nt':
            self.assertEqual(leftovers[0].stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == 'nt', 'POSIX parent fsync')
    def test_after_replace_fsync_failure_reports_uncertainty_without_replay(self):
        plan, raw = self.store.plan('Home', document('movie')), self.path.read_bytes()
        real_sync, directory_syncs = storage.os.fsync, []
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                directory_syncs.append(fd)
                if len(directory_syncs) == 2:
                    raise OSError(PRIVATE)
            return real_sync(fd)
        with mock.patch.object(storage.os, 'fsync', side_effect=sync):
            error = self.assert_error('commit_uncertain', self.store.apply, plan)
        self.assertTrue(error.replacement_occurred)
        self.assertEqual(self.current()['preset_favorites']['presets']['Home'], document('movie'))
        self.assertEqual(Path(error.backup_path).read_bytes(), raw)
        self.assert_error('config_changed', self.store.apply, plan)
        self.assertEqual(len(self.backups()), 1)

    def test_existing_backup_and_temporary_names_are_never_removed(self):
        plan, raw = self.store.plan('Home', document('movie')), self.path.read_bytes()
        backup = self.path.with_name(self.path.name + '.favorites-backup-' + plan.source_revision[:12] + '-' + '0' * 16 + '.json')
        backup.write_bytes(b'existing backup')
        with mock.patch.object(storage.secrets, 'token_hex', side_effect=lambda n: '0' * (2 * n)):
            self.assert_error('backup_failed', self.store.apply, plan)
        self.assertEqual(backup.read_bytes(), b'existing backup')
        backup.unlink()
        temporary = self.directory / ('.ott-favorites-' + '0' * 32)
        temporary.write_bytes(b'existing temporary')
        with mock.patch.object(storage.secrets, 'token_hex', side_effect=lambda n: '0' * (2 * n)):
            self.assert_error('write_failed', self.store.apply, plan)
        self.assertEqual(temporary.read_bytes(), b'existing temporary')
        self.assertEqual(self.path.read_bytes(), raw)

    def test_duplicate_json_nonfinite_and_oversized_configs_are_rejected(self):
        for raw in (b'{"presets":{},"presets":{}}', b'{"x":NaN}', b'{"x":1e999}', b'[]', b'\xff'):
            self.path.write_bytes(raw)
            self.assert_error('invalid_config', self.store.snapshot, 'Home')
        self.write(self.value)
        with mock.patch.object(storage, 'MAX_CONFIG_BYTES', 32):
            self.assert_error('too_large', self.store.snapshot, 'Home')

    def test_total_output_limit_is_checked_before_backup(self):
        limit = len(self.path.read_bytes()) + 40
        with mock.patch.object(storage, 'MAX_CONFIG_BYTES', limit):
            plan = self.store.plan('Home', document('x' * (limit // 2)))
            self.assert_error('too_large', self.store.apply, plan)
        self.assertEqual(self.backups(), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX privacy/links and flock')
    def test_unsafe_config_parent_and_lock_are_rejected_without_chmod(self):
        plan = self.store.plan('Home', document('movie'))
        self.path.chmod(0o644)
        self.assert_error('unsafe_path', self.store.snapshot, 'Home')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
        self.path.chmod(0o600)
        self.directory.chmod(0o755)
        self.assert_error('unsafe_path', self.store.snapshot, 'Home')
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o755)
        self.directory.chmod(0o700)
        lock = self.path.with_name(self.path.name + '.preset-favorites.lock')
        lock.write_bytes(b'lock')
        lock.chmod(0o644)
        self.assert_error('unsafe_lock', self.store.apply, plan)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.backups(), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink permissions')
    def test_symlink_directory_config_lock_and_nonregular_config_are_rejected(self):
        raw = self.path.read_bytes()
        target = self.path.with_name('real.json')
        self.path.rename(target)
        self.path.symlink_to(target)
        self.assert_error('read_failed', self.store.snapshot, 'Home')
        self.path.unlink()
        target.rename(self.path)
        alias = self.directory / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        self.assert_error('unsafe_path', self.make_store(alias / 'cli.json').snapshot, 'Home')
        alias.unlink()
        plan = self.store.plan('Home', document('movie'))
        lock = self.path.with_name(self.path.name + '.preset-favorites.lock')
        lock.symlink_to(self.path)
        self.assert_error('lock_unavailable', self.store.apply, plan)
        self.assertEqual(self.path.read_bytes(), raw)
        lock.unlink()
        self.path.unlink()
        self.path.mkdir(mode=0o700)
        with self.assertRaises(storage.StorageError):
            self.store.snapshot('Home')

    @unittest.skipIf(os.name == 'nt', 'POSIX hardlink and flock semantics')
    def test_hardlinked_lock_and_busy_lock_fail_before_backup(self):
        plan = self.store.plan('Home', document('movie'))
        lock = self.path.with_name(self.path.name + '.preset-favorites.lock')
        os.link(self.path, lock)
        with self.assertRaises(storage.StorageError):
            self.store.apply(plan)
        lock.unlink()
        with storage._Parent(self.directory) as parent, storage._Lock(parent, lock.name):
            self.assert_error('lock_unavailable', self.store.apply, plan)
        self.assertEqual(self.backups(), [])


if __name__ == '__main__':
    unittest.main()
