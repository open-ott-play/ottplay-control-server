#!/usr/bin/env python3
"""One USB ADB service, explicit device identities and serialized maintenance."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import signal
import subprocess
import sys
import time

from recovery import Recovery, RecoveryError


class ManagerError(RuntimeError):
    pass


def run(args, timeout=60):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagerError('Operation unavailable or timed out') from exc
    if result.returncode:
        raise ManagerError('Operation failed; no automatic device retry')
    return result.stdout


def config_read(path):
    config = json.loads(Path(path).read_text())
    if config.get('version') != 1 or not isinstance(config.get('devices'), dict):
        raise ManagerError('Invalid manager configuration')
    if not config['devices']:
        raise ManagerError('Empty registry')
    seen = set()
    for name, target in config['devices'].items():
        if not re.fullmatch('[a-z][a-z0-9_-]{0,31}', name):
            raise ManagerError('Invalid device alias')
        for key in ('serial', 'model', 'identity_value'):
            if not isinstance(target.get(key), str) or not target[key] or '\n' in target[key]:
                raise ManagerError('Incomplete device identity')
        if target.get('identity_property') not in ('android_id', 'ro.serialno'):
            raise ManagerError('Invalid identity property')
        if target['serial'] in seen:
            raise ManagerError('Duplicate serials are unsupported; do not guess a target')
        seen.add(target['serial'])
        for key in ('vid', 'pid'):
            if (key == 'vid' or target.get(key) is not None) and not re.fullmatch('[0-9a-f]{4}', target.get(key, '')):
                raise ManagerError('Invalid USB VID/PID')
        if type(target.get('sdk')) is not int or not 16 <= target['sdk'] <= 100:
            raise ManagerError('Invalid Android SDK')
        if type(target.get('max_attempts', 3)) is not int or not 1 <= target.get('max_attempts', 3) <= 10:
            raise ManagerError('Invalid recovery attempt bound')
        if type(target.get('auto_recover', False)) is not bool:
            raise ManagerError('Invalid recovery policy')
        if type(target.get('forward_port')) is not int or not 1024 <= target['forward_port'] <= 65535:
            raise ManagerError('Invalid per-device forward port')
        package = target.get('package', 'play.ott.foss')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', package):
            raise ManagerError('Invalid package')
    if len({t['forward_port'] for t in config['devices'].values()}) != len(config['devices']):
        raise ManagerError('Duplicate forwarding ports')
    if config.get('key_names') != ['fire', 'a1']:
        raise ManagerError('Provision both original identities explicitly')
    return config


@contextmanager
def lease(root, wait=30):
    # Linux is the only supported runtime; delay import for portable unit tests.
    import fcntl
    Path(root).mkdir(parents=True, exist_ok=True, mode=0o700)
    with (Path(root) / 'manager.lock').open('a') as lock:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ManagerError('Manager busy; no operation was started') from None
                time.sleep(.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def usb_inventory(config, root='/sys/bus/usb/devices'):
    candidates = []
    for path in Path(root).iterdir():
        try:
            values = {key: (path / key).read_text().strip() for key in
                      ('serial', 'product', 'idVendor', 'idProduct', 'busnum', 'devnum')}
        except FileNotFoundError:
            continue
        candidates.append(values)
    result = {}
    for alias, target in config['devices'].items():
        matches = [v for v in candidates if v['serial'] == target['serial']]
        if len(matches) > 1:
            # Exclude all ambiguous hardware, including a duplicate with another model.
            continue
        if not matches:
            continue
        device = matches[0]
        if any(target.get(expected) is not None and device[key] != target[expected] for key, expected in
               [('product', 'product'), ('idVendor', 'vid'), ('idProduct', 'pid')]):
            continue
        bus, number = int(device['busnum']), int(device['devnum'])
        if not (1 <= bus <= 999 and 1 <= number <= 999):
            raise ManagerError('Invalid USB address')
        result[alias] = f'/dev/bus/usb/{bus:03d}/{number:03d}'
    return result


class Target:
    def __init__(self, config, runner=run):
        self.config = config
        self.runner = runner
        self.transport = None

    def verify(self):
        records = []
        for line in self.runner(['adb', 'devices', '-l']).decode().splitlines():
            fields = line.split()
            if fields and fields[0] == self.config['serial']:
                records.append(fields)
        if len(records) != 1 or len(records[0]) < 2 or records[0][1] != 'device':
            raise ManagerError('Exact ADB target absent, unauthorized or ambiguous')
        fields = records[0]
        ids = [x.split(':', 1)[1] for x in fields if x.startswith('transport_id:')]
        if len(ids) != 1 or not ids[0].isdigit() or not any(x.startswith('usb:') for x in fields):
            raise ManagerError('Expected a unique USB transport')
        self.transport = ids[0]
        expected = [('ro.product.model', self.config['model']),
                    ('ro.build.version.sdk', str(self.config['sdk']))]
        for prop, value in expected:
            if self.adb('shell', 'getprop', prop).decode().strip() != value:
                raise ManagerError('Android identity mismatch')
        prop = self.config['identity_property']
        args = ('settings', 'get', 'secure', prop) if prop == 'android_id' else ('getprop', prop)
        if self.adb('shell', *args).decode().strip() != self.config['identity_value']:
            raise ManagerError('Android identity mismatch')

    def adb(self, *args):
        if self.transport is None:
            raise ManagerError('Unverified target')
        return self.runner(['adb', '-t', self.transport, *args])


def safe_args(args, target):
    # Global server/transport changes can disrupt other devices. Keep them out of this API.
    allowed = {'shell', 'exec-out', 'get-state', 'get-serialno', 'get-devpath',
               'install', 'install-multiple', 'uninstall', 'push', 'pull', 'forward'}
    if not args or args[0] not in allowed:
        raise ManagerError('Unsupported operation; server lifecycle belongs to the manager')
    if args[0] == 'forward':
        if '--remove-all' in args or '--list' in args:
            raise ManagerError('Global forward operation refused')
        first = 2 if len(args) > 1 and args[1] == '--remove' else 1
        if len(args) <= first or args[first] != 'tcp:' + str(target['forward_port']):
            raise ManagerError('Use a device-specific TCP forward port')
    return args


def command(config, root, alias, args, runner=run):
    if alias not in config['devices']:
        raise ManagerError('Unknown device alias')
    safe_args(args, config['devices'][alias])
    with lease(root):
        target = Target(config['devices'][alias], runner)
        target.verify()
        return target.adb(*args)


def status(config, root):
    results = {}
    with lease(root):
        for alias, target_config in config['devices'].items():
            try:
                target = Target(target_config)
                target.verify()
                state = Path(root) / alias / 'state.json'
                results[alias] = {'connected': True, 'state': json.loads(state.read_text()) if state.exists() else {}}
            except ManagerError as exc:
                results[alias] = {'connected': False, 'reason': str(exc)}
    return results


def serve(config, root, key_root='/keys', home_root='/tmp/adb-home'):
    home = Path(home_root) / '.android'
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in config['key_names']:
        for file in ('adbkey', 'adbkey.pub'):
            path = Path(key_root) / name / file
            if not path.is_file() or path.stat().st_size == 0:
                raise ManagerError('Provision original ADB keys; refusing identity generation')
    for file in ('adbkey', 'adbkey.pub'):
        shutil.copyfile(Path(key_root) / 'fire' / file, home / file)
        (home / file).chmod(0o600)
    os.environ['HOME'] = str(home.parent)
    os.environ['ADB_VENDOR_KEYS'] = str(Path(key_root) / 'a1/adbkey')
    # Foreground server is a supervised child. No host/network socket is published.
    adb = subprocess.Popen(['adb', 'server', 'nodaemon'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stopping = False
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    previous = {}
    try:
        while not stopping:
            if adb.poll() is not None:
                raise ManagerError('ADB foreground server exited')
            for alias, target_config in config['devices'].items():
                if not target_config.get('auto_recover'):
                    continue
                try:
                    with lease(root, wait=0):
                        target = Target(target_config)
                        result = Recovery(target, Path(root) / alias).tick()
                except (ManagerError, RecoveryError, OSError, ValueError) as exc:
                    result = {'result': 'unavailable', 'reason': str(exc)}
                if previous.get(alias) != result:
                    print(json.dumps({'device': alias, **result}), flush=True)
                    previous[alias] = result
            heartbeat = Path(root) / 'heartbeat.json'
            temporary = heartbeat.with_suffix('.tmp')
            temporary.write_text(json.dumps({'pid': os.getpid(), 'updated_at': time.time()}))
            temporary.replace(heartbeat)
            for _ in range(50):
                if stopping:
                    break
                time.sleep(.1)
    finally:
        adb.terminate()
        try:
            adb.wait(timeout=10)
        except subprocess.TimeoutExpired:
            adb.kill()
            adb.wait()


def desired_spec(config, config_path, nodes):
    if not re.fullmatch(r'(?:[^\s]+@)?sha256:[a-f0-9]{64}', config.get('image', '')):
        raise ManagerError('An immutable image digest is required')
    for key in ('key_dir', 'state_dir'):
        if not Path(config[key]).is_absolute() or ',' in config[key]:
            raise ManagerError('Use absolute mount paths without commas')
    return {'image': config['image'], 'keys': str(Path(config['key_dir']).resolve()),
            'state': str(Path(config['state_dir']).resolve()),
            'config': str(Path(config_path).resolve()),
            'config_sha256': hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
            'nodes': nodes}


def runtime_matches(details, spec):
    host = details.get('HostConfig', {})
    config = details.get('Config', {})
    mounts = {(m.get('Source'), m.get('Destination'), m.get('RW')) for m in details.get('Mounts', []) if m.get('Type') == 'bind'}
    desired_mounts = {(spec['keys'], '/keys', False), (spec['state'], '/state', True), (spec['config'], '/config.json', False)}
    devices = {(d.get('PathOnHost'), d.get('PathInContainer'), d.get('CgroupPermissions')) for d in host.get('Devices', [])}
    return (config.get('Image') == spec['image'] and config.get('User') == '0:0'
            and config.get('Entrypoint') == ['python3', '/app/manager.py']
            and config.get('Cmd') == ['--config', '/config.json', '--state-root', '/state', 'serve']
            and host.get('NetworkMode') == 'none' and host.get('ReadonlyRootfs')
            and not host.get('Privileged') and not host.get('CapAdd')
            and 'ALL' in (host.get('CapDrop') or [])
            and (host.get('SecurityOpt') or []) in (['no-new-privileges'], ['no-new-privileges:true'])
            and not host.get('DeviceCgroupRules') and not host.get('DeviceRequests')
            and not host.get('VolumesFrom') and not host.get('PortBindings')
            and host.get('PidMode', '') in ('', 'private') and host.get('IpcMode', '') in ('', 'private')
            and host.get('CpuShares') == 128 and host.get('Memory') == 268435456
            and host.get('MemorySwap') == 268435456 and host.get('PidsLimit') in (None, 0)
            and mounts == desired_mounts
            and len([m for m in details.get('Mounts', []) if m.get('Type') == 'bind']) == 3
            and not any(m.get('Type') != 'bind' and (m.get('Type'), m.get('Destination')) != ('tmpfs', '/tmp') for m in details.get('Mounts', []))
            and host.get('Tmpfs') == {'/tmp': 'rw,nosuid,nodev,size=134217728,mode=1777'}
            and devices == {(n, n, 'rw') for n in spec['nodes'].values()}
            and host.get('RestartPolicy', {}).get('Name') == 'no')


def reconcile(config, config_path):
    docker = config['docker_path']
    name = 'android-manager'
    root = config['state_dir']
    with lease(root, wait=0):
        nodes = usb_inventory(config)
        spec = desired_spec(config, config_path, nodes)
        signature = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        existing = run([docker, 'ps', '-a', '--filter', 'name=^/' + name + '$', '--format', '{{.Names}}']).decode().strip()
        if existing:
            details = json.loads(run([docker, 'inspect', name]))[0]
            labels = details['Config'].get('Labels') or {}
            if labels.get('org.ottplay.android-manager') != '1':
                raise ManagerError('Unrelated container occupies android-manager')
            if labels.get('org.ottplay.android-manager.spec') == signature and runtime_matches(details, spec):
                if not details['State']['Running']:
                    run([docker, 'start', name])
                return
            run([docker, 'stop', '-t', '15', name], timeout=30)
            run([docker, 'rm', name])
        args = [docker, 'run', '-d', '--name', name, '--label', 'org.ottplay.android-manager=1',
                '--label', 'org.ottplay.android-manager.spec=' + signature,
                '--restart', 'no', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--memory', '256m', '--memory-swap', '256m',
                '--cpu-shares', '128', '--user', '0:0',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=134217728,mode=1777',
                '--mount', f'type=bind,src={spec["keys"]},dst=/keys,readonly',
                '--mount', f'type=bind,src={spec["state"]},dst=/state',
                '--mount', f'type=bind,src={spec["config"]},dst=/config.json,readonly']
        for node in sorted(set(nodes.values())):
            args.extend(['--device', node + ':' + node + ':rw'])
        args.extend([config['image'], '--config', '/config.json', '--state-root', '/state', 'serve'])
        run(args)
        print(json.dumps({'result': 'reconciled', 'devices': sorted(nodes)}), flush=True)


def healthy(root):
    heartbeat = json.loads((Path(root) / 'heartbeat.json').read_text())
    if time.time() - heartbeat['updated_at'] > 180:
        raise ManagerError('Supervisor heartbeat expired')
    os.kill(heartbeat['pid'], 0)
    with socket.create_connection(('127.0.0.1', 5037), timeout=5):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--state-root', default='/state')
    sub = parser.add_subparsers(dest='mode', required=True)
    for mode in ('serve', 'host', 'status', 'reconcile', 'health'):
        sub.add_parser(mode)
    execute = sub.add_parser('exec')
    execute.add_argument('alias')
    execute.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    config = config_read(args.config)
    if args.mode == 'serve':
        serve(config, args.state_root)
    elif args.mode == 'health':
        healthy(args.state_root)
    elif args.mode == 'status':
        print(json.dumps(status(config, args.state_root)))
    elif args.mode == 'exec':
        sys.stdout.buffer.write(command(config, args.state_root, args.alias, args.arguments))
    elif args.mode == 'reconcile':
        reconcile(config, args.config)
    else:
        previous = None
        while True:
            try:
                config = config_read(args.config)
                reconcile(config, args.config)
                error = None
            except (ManagerError, OSError, ValueError) as exc:
                error = str(exc)
            if error and error != previous:
                print(json.dumps({'result': 'unavailable', 'reason': error}), flush=True)
            previous = error
            time.sleep(10)


if __name__ == '__main__':
    try:
        main()
    except (ManagerError, RecoveryError, OSError, ValueError) as exc:
        raise SystemExit(str(exc))
