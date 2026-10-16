#!/usr/bin/env python3
"""Prepare a signed native OTA release locally, then verify its direct HTTPS URLs.

The existing private Ed25519 key is read only by the repository's Go signer.
This tool never sends a device command or prints signing keys or release URLs.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

PACKAGE = 'play.ott.kitkat'
MAX_PAYLOAD = 64 * 1024 * 1024
MAX_HELPER = 4 * 1024 * 1024
MAX_BUNDLE_APK = 60 * 1024 * 1024
HERE = Path(__file__).resolve().parent


class InvalidRelease(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def https_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (len(value) <= 2048 and all(ord(c) > 32 for c in value)
                 and parsed.scheme == 'https' and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.fragment and not parsed.query
                 and (parsed.port is None or 0 < parsed.port < 65536))
    except ValueError:
        valid = False
    if not valid:
        raise InvalidRelease('Use direct HTTPS release URLs without credentials, query or fragment')
    return value


def bounded_file(path, limit):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise InvalidRelease('Input must be a bounded regular file')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) != info.st_size or len(data) > limit or (info.st_dev, info.st_ino, info.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
        raise InvalidRelease('Input changed while reading')
    return data


def arm_elf(raw):
    return len(raw) >= 20 and raw[:6] == b'\x7fELF\x01\x01' and raw[18:20] == b'\x28\x00'


def helper_elf(raw):
    return (len(raw) >= 52 and arm_elf(raw) and raw[6] == 1
            and raw[16:18] in (b'\x02\x00', b'\x03\x00')
            and raw[20:24] == b'\x01\x00\x00\x00')


def zip_entries(raw, expected=None):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        rows = archive.infolist()
        names = [row.filename for row in rows]
        if len(names) != len(set(names)) or (expected is not None and set(names) != set(expected)):
            raise InvalidRelease('Archive entries must be unique and match the release format')
        for row in rows:
            mode = row.external_attr >> 16
            if (row.flag_bits & 1 or (expected is not None and row.is_dir())
                    or stat.S_IFMT(mode) not in ((0, stat.S_IFREG) if expected is not None else (0, stat.S_IFREG, stat.S_IFDIR))
                    or row.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or row.file_size > MAX_PAYLOAD):
                raise InvalidRelease('Unsupported archive entry')
        return archive
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidRelease('Invalid ZIP payload') from exc


def manifest_package(raw):
    """Read only the root package attribute from Android's bounded binary XML."""
    bad = InvalidRelease('Unsupported APK binary manifest')
    if len(raw) < 8 or len(raw) > 2 * 1024 * 1024:
        raise bad
    u16 = lambda b, p=0: struct.unpack_from('<H', b, p)[0]
    u32 = lambda b, p=0: struct.unpack_from('<I', b, p)[0]
    if u16(raw) != 3 or u16(raw, 2) != 8 or u32(raw, 4) != len(raw):
        raise bad
    pool = None
    offset = 8
    while offset < len(raw):
        if len(raw) - offset < 8:
            raise bad
        kind, header, size = struct.unpack_from('<HHI', raw, offset)
        if header < 8 or size < header or offset + size > len(raw):
            raise bad
        chunk = raw[offset:offset + size]
        offset += size
        if kind == 1:
            if pool is not None or header < 28:
                raise bad
            count, flags, start = u32(chunk, 8), u32(chunk, 16), u32(chunk, 20)
            if not 1 <= count <= min(65536, (size - header) // 4) or not header + count * 4 <= start < size:
                raise bad
            pool = []
            for index in range(count):
                location = start + u32(chunk, header + index * 4)
                if location >= size:
                    raise bad
                value = chunk[location:]
                def length8(value):
                    if not value:
                        raise bad
                    if value[0] & 128:
                        if len(value) < 2:
                            raise bad
                        return ((value[0] & 127) << 8) | value[1], value[2:]
                    return value[0], value[1:]
                if flags & 256:
                    _, value = length8(value)
                    length, value = length8(value)
                    if length >= len(value) or value[length] != 0:
                        raise bad
                    pool.append(value[:length].decode('utf-8'))
                else:
                    if len(value) < 2:
                        raise bad
                    length, value = u16(value), value[2:]
                    if length & 0x8000:
                        if len(value) < 2:
                            raise bad
                        length, value = ((length & 0x7fff) << 16) | u16(value), value[2:]
                    if length * 2 + 2 > len(value) or value[length * 2:length * 2 + 2] != b'\0\0':
                        raise bad
                    pool.append(value[:length * 2].decode('utf-16le'))
        if kind == 0x102:
            if header < 16 or size - header < 20 or pool is None:
                raise bad
            ext = chunk[header:]
            name, attr_start, attr_size, count = u32(ext, 4), u16(ext, 8), u16(ext, 10), u16(ext, 12)
            if name >= len(pool) or pool[name] != 'manifest' or attr_start < 20 or attr_size < 20 or attr_start > len(ext) or count > (len(ext) - attr_start) // attr_size:
                raise bad
            found = None
            for index in range(count):
                attr = ext[attr_start + index * attr_size:]
                name = u32(attr, 4)
                if name >= len(pool):
                    raise bad
                if pool[name] != 'package':
                    continue
                if u32(attr) != 0xffffffff or found is not None:
                    raise bad
                value = u32(attr, 8)
                if value == 0xffffffff:
                    if attr[15] != 3:
                        raise bad
                    value = u32(attr, 16)
                if value >= len(pool):
                    raise bad
                found = pool[value]
            if not found:
                raise bad
            return found
    raise bad


def apk_package(raw):
    try:
        with zip_entries(raw) as archive:
            row = archive.getinfo('AndroidManifest.xml')
            if row.file_size > 2 * 1024 * 1024:
                raise InvalidRelease('APK manifest is too large')
            return manifest_package(archive.read(row))
    except (KeyError, UnicodeError, struct.error, zipfile.BadZipFile) as exc:
        raise InvalidRelease('Invalid APK manifest') from exc


def embedded_guard(apk):
    with zip_entries(apk) as source:
        try:
            asset = source.getinfo('assets/ott-touch-guard')
        except KeyError as exc:
            raise InvalidRelease('APK has no embedded touch helper') from exc
        mode = stat.S_IFMT(asset.external_attr >> 16)
        if asset.is_dir() or mode not in (0, stat.S_IFREG) or not 52 <= asset.file_size <= MAX_HELPER:
            raise InvalidRelease('APK helper must be a bounded regular ARM ELF file')
        helper = source.read(asset)
    if not helper_elf(helper):
        raise InvalidRelease('APK helper must be a bounded regular ARM ELF file')
    return helper


def validate_payload(kind, raw):
    if not 0 < len(raw) <= MAX_PAYLOAD:
        raise InvalidRelease('Payload exceeds the native OTA limit')
    if kind == 'agent':
        if not arm_elf(raw):
            raise InvalidRelease('Expected an ARM32 little-endian ELF agent')
    elif kind == 'apk':
        if len(raw) > MAX_BUNDLE_APK:
            raise InvalidRelease('APK exceeds the supported 60 MiB limit')
        if apk_package(raw) != PACKAGE:
            raise InvalidRelease('APK has the wrong package')
        embedded_guard(raw)
    elif kind == 'bundle':
        with zip_entries(raw, {'app.apk', 'ott-touch-guard'}) as archive:
            helper_row = archive.getinfo('ott-touch-guard')
            if not 0 < archive.getinfo('app.apk').file_size <= MAX_BUNDLE_APK:
                raise InvalidRelease('Bundle APK exceeds its size limit')
            if not 0 < helper_row.file_size <= MAX_HELPER:
                raise InvalidRelease('Touch helper exceeds its size limit')
            helper = archive.read(helper_row)
            apk = archive.read('app.apk')
        if not helper_elf(helper) or apk_package(apk) != PACKAGE:
            raise InvalidRelease('Bundle components have the wrong identity')
        if embedded_guard(apk) != helper:
            raise InvalidRelease('APK and system touch helper must match exactly')
    else:
        raise InvalidRelease('Unsupported update kind')


def bundle_from_apk(apk):
    validate_payload('apk', apk)
    if len(apk) > MAX_BUNDLE_APK:
        raise InvalidRelease('Bundle APK exceeds its size limit')
    helper = embedded_guard(apk)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in [('app.apk', apk), ('ott-touch-guard', helper)]:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entry.compress_type = zipfile.ZIP_STORED
            archive.writestr(entry, raw)
    raw = output.getvalue()
    validate_payload('bundle', raw)
    return raw


def private_write(path, raw):
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(args):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', args.version):
        raise InvalidRelease('Invalid release version')
    https_url(args.url)
    https_url(args.manifest_url)
    key = args.key.lstat()
    if not stat.S_ISREG(key.st_mode) or stat.S_IMODE(key.st_mode) != 0o600 or key.st_uid != os.getuid() or key.st_size != 64:
        raise InvalidRelease('Signing key must be an owned regular 0600 Ed25519 private-key file')
    if getattr(args, 'apk', None) is not None:
        if args.kind != 'bundle':
            raise InvalidRelease('--apk is only supported for --kind bundle')
        raw = bundle_from_apk(bounded_file(args.apk, MAX_BUNDLE_APK))
    else:
        raw = bounded_file(args.payload, MAX_PAYLOAD)
    validate_payload(args.kind, raw)
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    os.chmod(output, 0o700)
    payload_name = {'agent':'agent', 'apk':'app.apk', 'bundle':'bundle.zip'}[args.kind]
    private_write(output / payload_name, raw)
    manifest = {'schema':1, 'kind':args.kind, 'version':args.version, 'package':PACKAGE,
                'url':args.url, 'sha256':sha256(raw), 'size':len(raw)}
    private_write(output / 'manifest.json', (json.dumps(manifest, sort_keys=True, separators=(',', ':'))+'\n').encode())
    result = subprocess.run(['go', 'run', './cmd/sign-update', 'sign', str(args.key.resolve()),
                             str(output / 'manifest.json'), str(output / 'signed.json')],
                            cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
    if result.returncode != 0:
        raise InvalidRelease('Local signer failed; no device command was sent')
    os.chmod(output / 'signed.json', 0o600)
    envelope = bounded_file(output / 'signed.json', 16384)
    digest = sha256(envelope)
    if result.stdout.decode('ascii', errors='replace').strip() != digest:
        raise InvalidRelease('Signer output digest did not match the signed envelope')
    release = {'kind':args.kind, 'version':args.version, 'payload_file':payload_name,
               'payload_url':args.url, 'manifest_url':args.manifest_url, 'size':len(raw),
               'payload_sha256':sha256(raw), 'envelope_sha256':digest}
    private_write(output / 'release.json', (json.dumps(release, sort_keys=True, indent=2)+'\n').encode())
    return {key:release[key] for key in ('kind', 'version', 'size', 'payload_sha256', 'envelope_sha256')}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise InvalidRelease('Release URL redirects; publish at a direct HTTPS URL')


def verify(args):
    release = json.loads(bounded_file(args.directory / 'release.json', 16384))
    opener = urllib.request.build_opener(NoRedirect())
    for file_key, url_key, hash_key, limit in [('payload_file','payload_url','payload_sha256',MAX_PAYLOAD),
                                             (None,'manifest_url','envelope_sha256',16384)]:
        name = release[file_key] if file_key else 'signed.json'
        if name not in ('agent', 'app.apk', 'bundle.zip', 'signed.json'):
            raise InvalidRelease('Invalid release file')
        local = bounded_file(args.directory / name, limit)
        if sha256(local) != release[hash_key]:
            raise InvalidRelease('Local release bytes changed')
        request = urllib.request.Request(https_url(release[url_key]), headers={'User-Agent':'ottplay-native-ota-verifier/1'})
        with opener.open(request, timeout=30) as response:
            if response.status != 200:
                raise InvalidRelease('Published release is unavailable')
            published = response.read(limit + 1)
        if published != local:
            raise InvalidRelease('Published bytes do not match the reviewed release')
    return {'verified':True, 'kind':release['kind'], 'version':release['version'],
            'payload_sha256':release['payload_sha256'], 'envelope_sha256':release['envelope_sha256']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('prepare')
    build.add_argument('--kind', required=True, choices=('agent','apk','bundle'))
    build.add_argument('--version', required=True)
    source = build.add_mutually_exclusive_group(required=True)
    source.add_argument('--payload', type=Path, help='Agent, APK, or an existing validated bundle ZIP')
    source.add_argument('--apk', type=Path, help='Build a deterministic bundle from this APK and its embedded helper')
    build.add_argument('--url', required=True)
    build.add_argument('--manifest-url', required=True)
    build.add_argument('--key', required=True, type=Path)
    build.add_argument('--output', required=True, type=Path)
    check = commands.add_parser('verify')
    check.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare(args) if args.command == 'prepare' else verify(args)
    except InvalidRelease as exc:
        print('Error: '+str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, urllib.error.URLError, zipfile.BadZipFile, EOFError, zlib.error):
        print('Error: Release preparation or verification failed; no device command was sent', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
