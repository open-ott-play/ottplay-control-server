"""Local EPG fallback policy and per-user sidecar installation contracts."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fallback = load('local_epg_fallback', ROOT / 'cli/epg_fallback.py')
installer = load('local_epg_installer', ROOT / 'scripts/install-local-epg.py')
PRIMARY = {'url': 'https://epg.example/epg/v1', 'source': 'epg-one'}
LOCAL = 'http://127.0.0.1:18792/epg/v1'
SOURCE = 'a' * 40


class FallbackPolicyTest(unittest.TestCase):
    def test_existing_configuration_stays_primary_only(self):
        source = copy.deepcopy(PRIMARY)
        primary, local = fallback.split_settings(source)
        self.assertEqual((primary, local), (PRIMARY, None))
        primary['url'] = 'changed'
        self.assertEqual(source, PRIMARY)

    def test_explicit_fallback_is_literal_loopback_and_does_not_mutate_settings(self):
        for url in (LOCAL, 'http://127.1.2.3:18792/epg/v1/', 'https://[::1]:18792/epg/v1'):
            with self.subTest(url=url):
                original = {**PRIMARY, 'fallback_url': url}
                primary, local = fallback.split_settings(original)
                self.assertEqual(primary, PRIMARY)
                self.assertEqual(local, {'url': url.rstrip('/'), 'source': 'epg-one'})
                self.assertEqual(original['fallback_url'], url)

    def test_nonlocal_ambiguous_or_credentialled_fallbacks_are_rejected(self):
        for url in ('http://localhost:18792/epg/v1', 'http://192.168.1.1/epg/v1',
                    'http://2130706433/epg/v1', 'http://127.1/epg/v1',
                    'http://127.0.0.1:0/epg/v1', 'http://127.0.0.1:99999/epg/v1',
                    'http://user:secret@127.0.0.1/epg/v1', 'http://127.0.0.1/epg/v1?',
                    'http://127.0.0.1/epg/v1#', 'http://127.0.0.1/private',
                    'http://127.0.0.1/epg/%76%31', 'http://[::1%lo0]/epg/v1',
                    'http://127.0.0.1/epg/v1\n', 'file:///epg/v1', '', None):
            with self.subTest(url=url), self.assertRaises(fallback.ConfigurationError) as error:
                fallback.split_settings({**PRIMARY, 'fallback_url': url})
            self.assertNotIn('secret', str(error.exception))

    def test_invalid_extra_fields_and_self_fallback_are_rejected(self):
        for settings in (None, {}, {**PRIMARY, 'source': 'private'}, {**PRIMARY, 'typo': LOCAL},
                         {'url': LOCAL, 'source': 'epg-one', 'fallback_url': LOCAL + '/'},
                         {'url': 'http://127.0.0.1/epg/v1', 'source': 'epg-one',
                          'fallback_url': 'http://127.0.0.1:80/epg/v1'},
                         {**PRIMARY, 'url': 'https://user:secret@epg.example/epg/v1'}):
            with self.subTest(settings=settings), self.assertRaises(fallback.ConfigurationError):
                fallback.split_settings(settings)

    def test_local_transport_detection_does_not_trust_dns_or_other_paths(self):
        self.assertTrue(fallback.is_local_url(LOCAL))
        self.assertTrue(fallback.is_local_url('http://[::1]/epg/v1'))
        for value in ('http://localhost/epg/v1', 'https://epg.example/epg/v1',
                      'http://127.0.0.1/private', None):
            self.assertFalse(fallback.is_local_url(value))

    def test_availability_only_requires_explicit_typed_classification(self):
        for status in (429, 500, 502, 503, 504):
            self.assertTrue(fallback.eligible_failure(status=status))
        for status in (True, False, 200, 400, 401, 403, 404, 409, 413, 422, '503', 503.0):
            self.assertFalse(fallback.eligible_failure(status=status))
        self.assertTrue(fallback.eligible_failure(transport=True))
        self.assertFalse(fallback.eligible_failure())
        self.assertFalse(fallback.eligible_failure(transport='timeout'))
        self.assertFalse(fallback.eligible_failure(status=403, transport=True))


class SidecarInstallerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name).resolve()
        self.paths = installer.Paths(self.home)
        self.binary = self.home / 'source-server'
        self.binary.write_bytes(b'qualified native binary')
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.running = mock.patch.object(installer, 'running', return_value=False).start()
        self.launch = mock.patch.object(installer, '_launchctl', return_value=True).start()
        self.port = mock.patch.object(installer, '_port_free').start()
        self.addCleanup(mock.patch.stopall)

    def install(self, **kwargs):
        return installer.install(self.paths, self.binary, SOURCE, self.digest, **kwargs)

    def test_install_uses_separate_loopback_process_and_verified_immutable_bytes(self):
        metadata = self.install()
        self.assertEqual(installer.owned(self.paths), metadata)
        self.assertEqual(self.paths.binary(SOURCE).read_bytes(), self.binary.read_bytes())
        self.assertEqual(self.paths.binary(SOURCE).stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.paths.manifest.stat().st_mode & 0o777, 0o600)
        value = plistlib.loads(self.paths.agent.read_bytes())
        self.assertEqual(value['ProgramArguments'], [str(self.paths.binary(SOURCE)), '--host', '127.0.0.1', '--port', '18792'])
        self.assertEqual(value['EnvironmentVariables'], {'EPG_ONLY': 'true', 'EPG_CACHE_DIR': str(self.paths.cache)})
        self.port.assert_called_once_with(18792)
        self.launch.assert_called_once_with('bootstrap', installer._target(), str(self.paths.agent))

    def test_sha_mismatch_never_creates_installation_or_stops_service(self):
        with self.assertRaisesRegex(installer.InstallError, 'SHA-256'):
            installer.install(self.paths, self.binary, SOURCE, '0' * 64)
        self.assertFalse(self.paths.root.exists())
        self.launch.assert_not_called()

    def test_occupied_port_preserves_original_files(self):
        self.port.side_effect = installer.InstallError('port already in use')
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse(self.paths.root.exists())
        self.launch.assert_not_called()

    def test_foreign_agent_and_foreign_runtime_are_not_overwritten(self):
        self.paths.agent.parent.mkdir(parents=True)
        self.paths.agent.write_bytes(b'foreign agent')
        with self.assertRaisesRegex(installer.InstallError, 'ownership'):
            self.install()
        self.assertEqual(self.paths.agent.read_bytes(), b'foreign agent')
        self.paths.agent.unlink()
        self.paths.root.mkdir(parents=True)
        marker = self.paths.root / 'private-file'
        marker.write_bytes(b'keep')
        with self.assertRaisesRegex(installer.InstallError, 'ownership'):
            self.install()
        self.assertEqual(marker.read_bytes(), b'keep')
        self.launch.assert_not_called()

    def test_user_modified_owned_plist_is_preserved(self):
        self.install(start=False)
        self.paths.agent.write_bytes(b'user changed agent')
        with self.assertRaisesRegex(installer.InstallError, 'modified'):
            installer.uninstall(self.paths)
        self.assertEqual(self.paths.agent.read_bytes(), b'user changed agent')
        self.launch.assert_not_called()

    def test_bootstrap_failure_retains_inspectable_first_installation(self):
        self.launch.side_effect = installer.InstallError('bootstrap failed')
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(installer.owned(self.paths)['source_sha'], SOURCE)
        self.assertTrue(self.paths.binary(SOURCE).is_file())

    def test_update_bootstrap_failure_restores_existing_configuration(self):
        self.install(start=False)
        old_manifest, old_agent = self.paths.manifest.read_bytes(), self.paths.agent.read_bytes()
        self.running.return_value = True
        new_source = 'b' * 40
        failures = [True, installer.InstallError('bootstrap failed'), True]
        self.launch.side_effect = failures
        with self.assertRaises(installer.InstallError):
            installer.install(self.paths, self.binary, new_source, self.digest)
        self.assertEqual(self.paths.manifest.read_bytes(), old_manifest)
        self.assertEqual(self.paths.agent.read_bytes(), old_agent)
        self.assertEqual(self.launch.call_args_list[-1], mock.call('bootstrap', installer._target(), str(self.paths.agent), check=False))

    def test_symlink_log_and_parent_directories_are_rejected(self):
        foreign = self.home / 'foreign'
        foreign.write_bytes(b'private')
        self.paths.logs.mkdir(parents=True)
        (self.paths.logs / 'stderr.log').symlink_to(foreign)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(foreign.read_bytes(), b'private')
        self.launch.assert_not_called()

    def test_uninstall_preserves_cache_and_ownership_for_later_reinstall(self):
        self.install(start=False)
        cache = self.paths.cache / 'public-guide'
        cache.write_bytes(b'cached public data')
        result = installer.uninstall(self.paths)
        self.assertFalse(self.paths.agent.exists())
        self.assertEqual(cache.read_bytes(), b'cached public data')
        self.assertEqual(installer.owned(self.paths)['source_sha'], SOURCE)
        self.assertTrue(result['cache_preserved'])
        self.install(start=False)
        self.assertTrue(self.paths.agent.exists())

    def test_immutable_revision_rejects_different_existing_binary(self):
        self.install(start=False)
        self.paths.binary(SOURCE).write_bytes(b'changed')
        with self.assertRaisesRegex(installer.InstallError, 'SHA-256'):
            self.install(start=False)

    def test_status_does_not_interpret_untyped_or_stale_health_as_ready(self):
        self.install(start=False)
        self.running.return_value = True
        for value in ([], {'version': True, 'source': 'epg-one', 'stale': False},
                      {'version': 1, 'source': 'epg-one', 'stale': True},
                      {'version': 1, 'source': 'epg-one', 'stale': False}):
            context = mock.MagicMock()
            context.__enter__.return_value.read.return_value = json.dumps(value).encode()
            opener = mock.Mock()
            opener.open.return_value = context
            with mock.patch.object(installer.urllib.request, 'build_opener', return_value=opener):
                result = installer.status(self.paths)
            self.assertEqual(result['ready'], isinstance(value, dict) and type(value.get('version')) is int and value.get('stale') is False)


if __name__ == '__main__':
    unittest.main()
