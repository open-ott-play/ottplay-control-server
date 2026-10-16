import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare_update', HERE / 'prepare_update.py')
ota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ota)
ELF = b'\x7fELF\x01\x01\x01' + bytes(9) + b'\x02\x00\x28\x00\x01\x00\x00\x00' + bytes(28)


def manifest(package=ota.PACKAGE):
    strings = ['manifest', 'package', package]
    pool = bytearray(28 + len(strings) * 4)
    struct.pack_into('<HH', pool, 0, 1, 28)
    struct.pack_into('<I', pool, 8, len(strings))
    struct.pack_into('<I', pool, 16, 256)
    struct.pack_into('<I', pool, 20, len(pool))
    start = len(pool)
    for index, value in enumerate(strings):
        struct.pack_into('<I', pool, 28 + index * 4, len(pool) - start)
        data = value.encode()
        pool.extend(bytes([len(data), len(data)]) + data + b'\0')
    struct.pack_into('<I', pool, 4, len(pool))
    tag = bytearray(56)
    struct.pack_into('<HHI', tag, 0, 0x102, 16, len(tag))
    struct.pack_into('<IIHHH', tag, 16, 0xffffffff, 0, 20, 20, 1)
    struct.pack_into('<IIIHBBI', tag, 36, 0xffffffff, 1, 2, 8, 0, 3, 2)
    return struct.pack('<HHI', 3, 8, 8 + len(pool) + len(tag)) + pool + tag


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as result:
        for name, value in entries:
            result.writestr(name, value)
    return output.getvalue()


class PrepareUpdateTests(unittest.TestCase):
    def test_binary_manifest_and_truncation(self):
        raw = manifest()
        self.assertEqual(ota.manifest_package(raw), ota.PACKAGE)
        for index in range(len(raw)):
            with self.assertRaises(ota.InvalidRelease):
                ota.manifest_package(raw[:index])

    def test_payload_and_bundle_identity(self):
        apk = archive([('AndroidManifest.xml', manifest()), ('assets/ott-touch-guard', ELF)])
        ota.validate_payload('agent', ELF)
        ota.validate_payload('apk', apk)
        ota.validate_payload('bundle', archive([('app.apk', apk), ('ott-touch-guard', ELF)]))
        built = ota.bundle_from_apk(apk)
        self.assertEqual(built, ota.bundle_from_apk(apk))
        ota.validate_payload('bundle', built)
        with zipfile.ZipFile(io.BytesIO(built)) as check:
            self.assertEqual(check.namelist(), ['app.apk', 'ott-touch-guard'])
            self.assertEqual(check.read('app.apk'), apk)
            self.assertEqual(check.read('ott-touch-guard'), ELF)
        bad = [
            ('agent', b'not an ELF'),
            ('apk', archive([('AndroidManifest.xml', manifest('other.app'))])),
            ('bundle', archive([('app.apk', apk), ('ott-touch-guard', ELF + b'x')])),
            ('bundle', archive([('app.apk', apk), ('ott-touch-guard', ELF), ('unexpected', b'x')])),
        ]
        for kind, raw in bad:
            with self.subTest(kind=kind), self.assertRaises(ota.InvalidRelease):
                ota.validate_payload(kind, raw)

    def test_standalone_apk_rejects_invalid_helper_before_signing(self):
        directory = zipfile.ZipInfo('assets/ott-touch-guard')
        directory.create_system = 3
        directory.external_attr = (stat.S_IFDIR | 0o755) << 16
        symlink = zipfile.ZipInfo('assets/ott-touch-guard')
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        bad_helpers = {
            'missing': [],
            'not ELF': [('assets/ott-touch-guard', b'x' * 64)],
            'truncated': [('assets/ott-touch-guard', ELF[:20])],
            'wrong ELF type': [('assets/ott-touch-guard', ELF[:16] + b'\x01\x00' + ELF[18:])],
            'too large': [('assets/ott-touch-guard', ELF + bytes(ota.MAX_HELPER))],
            'directory': [(directory, ELF)],
            'symlink': [(symlink, ELF)],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / 'unused.key'
            key.write_bytes(bytes(64))
            key.chmod(0o600)
            for label, entries in bad_helpers.items():
                with self.subTest(helper=label):
                    apk = archive([('AndroidManifest.xml', manifest())] + entries)
                    payload = root / 'candidate.apk'
                    payload.write_bytes(apk)
                    args = argparse.Namespace(kind='apk', version='0.10', payload=payload,
                        url='https://example.test/app.apk', manifest_url='https://example.test/signed.json',
                        key=key, output=root / 'release')
                    with mock.patch.object(ota.subprocess, 'run') as signer:
                        with self.assertRaises(ota.InvalidRelease):
                            ota.prepare(args)
                        signer.assert_not_called()
                    self.assertFalse(args.output.exists())

    def test_urls_fail_before_any_signing(self):
        for value in ['http://example.test/a', 'https://u:p@example.test/a', 'https://example.test/a?token=x', 'https://example.test/a#f', 'https://[bad/a', 'https://example.test:99999/a', 'https://example.test/a\n']:
            with self.assertRaises(ota.InvalidRelease):
                ota.https_url(value)
        with self.assertRaises(ota.InvalidRelease):
            ota.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_real_signer_private_artifacts_and_existing_directory_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / 'test.key'
            subprocess.run(['go', 'run', './cmd/sign-update', 'generate', str(key)], cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            payload = root / 'agent'
            payload.write_bytes(ELF)
            args = argparse.Namespace(kind='agent', version='0.1.2-beta.1', payload=payload,
                url='https://example.test/agent', manifest_url='https://example.test/signed.json', key=key, output=root/'release')
            result = ota.prepare(args)
            self.assertEqual(result['payload_sha256'], ota.sha256(ELF))
            self.assertEqual(stat.S_IMODE(args.output.stat().st_mode), 0o700)
            for path in args.output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            envelope = (args.output/'signed.json').read_bytes()
            self.assertEqual(result['envelope_sha256'], ota.sha256(envelope))
            with self.assertRaises(FileExistsError):
                ota.prepare(args)
            self.assertNotIn('url', result)
            self.assertNotIn('key', json.dumps(result))
            # verify compares exact bytes from both direct HTTPS URLs.
            responses = [mock.MagicMock(), mock.MagicMock()]
            for response, raw in zip(responses, [ELF, envelope]):
                response.__enter__.return_value = response
                response.status = 200
                response.read.return_value = raw
            opener = mock.Mock()
            opener.open.side_effect = responses
            with mock.patch.object(ota.urllib.request, 'build_opener', return_value=opener):
                self.assertTrue(ota.verify(argparse.Namespace(directory=args.output))['verified'])
            self.assertEqual(opener.open.call_count, 2)
            responses[0].read.return_value = b'changed'
            opener.open.side_effect = responses
            with mock.patch.object(ota.urllib.request, 'build_opener', return_value=opener), self.assertRaises(ota.InvalidRelease):
                ota.verify(argparse.Namespace(directory=args.output))

    def test_failure_output_does_not_include_paths(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = ota.main(['verify', '--directory', '/private/secret-token-missing'])
        self.assertEqual(result, 1)
        self.assertNotIn('secret-token', output.getvalue())


if __name__ == '__main__':
    unittest.main()
