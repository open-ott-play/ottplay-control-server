#!/usr/bin/env python3
"""Explicit-target USB Android recovery. No network ADB or general shell API."""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time


UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


class RecoveryError(RuntimeError):
    pass


def validate_capture_name(name):
    """Accept one portable PNG filename, never an operator-selected host path."""
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.png', name):
        raise RecoveryError('Screenshot requires a bare PNG filename using letters, digits, _ or -')
    return name


def save_capture(state_dir, name, capture):
    """Reserve a private, confined output before requesting bytes from a device."""
    validate_capture_name(name)
    root = Path(state_dir)
    try:
        with ExitStack() as stack:
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, root_fd)
            try:
                os.mkdir('captures', mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            captures_fd = os.open(
                'captures', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
            )
            stack.callback(os.close, captures_fd)
            os.fchmod(captures_fd, 0o700)
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=captures_fd,
            )
            complete = False
            try:
                with os.fdopen(fd, 'wb') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    data = capture()
                    if (
                        not isinstance(data, bytes) or not data.startswith(b'\x89PNG\r\n\x1a\n')
                        or len(data) > 32 * 1024 * 1024
                    ):
                        raise RecoveryError('Invalid screenshot')
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(captures_fd)
                complete = True
            finally:
                if not complete:
                    os.unlink(name, dir_fd=captures_fd)
        return {'result': 'captured', 'path': str(root / 'captures' / name)}
    except (OSError, subprocess.TimeoutExpired):
        raise RecoveryError('Screenshot unavailable, timed out or could not be saved privately') from None


def save_recovery_state(state_dir, state):
    """Atomically persist a checkpoint, including its containing directory."""
    try:
        with ExitStack() as stack:
            root_fd = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, root_fd)
            temporary = '.state-' + secrets.token_hex(16)
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=root_fd,
            )
            pending = True
            try:
                with os.fdopen(fd, 'w') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump(state, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, 'state.json', src_dir_fd=root_fd, dst_dir_fd=root_fd)
                pending = False
                os.fsync(root_fd)
            finally:
                if pending:
                    os.unlink(temporary, dir_fd=root_fd)
    except (OSError, ValueError, TypeError):
        raise RecoveryError('Recovery state could not be saved durably') from None


def secure_keyguard(policy, trust):
    """Only explicit, consistent keyguard security evidence authorizes a swipe."""
    secure = re.findall(r'\b(?:isDeviceSecure|deviceSecure|mSecure|secure)\s*[=:]\s*(true|false)\b', trust, re.I)
    if not secure or any(v.lower() != 'false' for v in secure):
        raise RecoveryError('Secure keyguard state is unknown or protected; local unlock required')
    showing = re.findall(r'\bmShowingLockscreen\s*=\s*(true|false)\b', policy)
    if len(set(showing)) != 1:
        raise RecoveryError('Lockscreen visibility is ambiguous')
    return showing[0] == 'true'


def probe_keyguard(text):
    """Read only the fixed, shell-permission-protected probe broadcast result."""
    match = re.search(r'Broadcast completed:.*?\bdata=(.*)$', text, re.M)
    if not match:
        raise RecoveryError('Keyguard probe returned no verified state')
    raw = match.group(1).strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    try:
        data = json.loads(raw.replace('\\"', '"'))
    except (ValueError, TypeError):
        raise RecoveryError('Invalid keyguard probe response') from None
    if not isinstance(data, dict) or set(data) != {'secure', 'locked'} or any(type(data[k]) is not bool for k in data):
        raise RecoveryError('Incomplete keyguard probe response')
    if data['secure']:
        raise RecoveryError('Secure keyguard requires local authentication')
    return data['locked']


def display_size(text):
    values = re.findall(r'(?:Physical|Override) size:\s*(\d+)x(\d+)', text)
    if not values:
        raise RecoveryError('Display size unavailable')
    width, height = map(int, values[-1])
    if not (100 <= width <= 10000 and 100 <= height <= 10000):
        raise RecoveryError('Display size invalid')
    return width, height


class Supervisor:
    def __init__(self, config, runner=None, sleep=time.sleep):
        self.c = config
        target = config.get('target', {})
        if config.get('version') != 1 or set(target) != {'serial', 'product', 'model', 'sdk', 'android_id'}:
            raise RecoveryError('Invalid target configuration')
        if not all(isinstance(target[k], str) and target[k] and '\n' not in target[k] for k in ('serial', 'product', 'model')):
            raise RecoveryError('Invalid target identity')
        if not re.fullmatch('[0-9a-f]{16}', target['android_id']) or type(target['sdk']) is not int:
            raise RecoveryError('Invalid Android identity')
        for key in ('docker_path', 'image', 'adb_key_dir', 'container_prefix', 'state_dir'):
            if not isinstance(config.get(key), str) or not config[key]:
                raise RecoveryError('Missing configuration: ' + key)
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{2,60}', config['container_prefix']):
            raise RecoveryError('Invalid owned container prefix')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', config.get('package', 'play.ott.foss')):
            raise RecoveryError('Invalid package')
        if not 1 <= config.get('max_attempts', 3) <= 10 or not 1 <= config.get('poll_seconds', 5) <= 300:
            raise RecoveryError('Invalid retry policy')
        timeout = config.get('command_timeout_seconds', 60)
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise RecoveryError('Invalid command timeout')
        self.root = Path(config['state_dir'])
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.runner = runner or self._run
        self.sleep = sleep
        self.container = None
        self.node = None

    def _run(self, argv):
        try:
            result = subprocess.run(argv, capture_output=True, timeout=self.c.get("command_timeout_seconds", 60))
        except (OSError, subprocess.TimeoutExpired):
            raise RecoveryError('Transport operation unavailable or timed out') from None
        if result.returncode:
            raise RecoveryError('Transport operation failed; inspect private host logs')
        return result.stdout.decode(errors='replace').strip()

    def docker(self, *args):
        return self.runner([self.c['docker_path'], *args])

    def usb(self):
        found = []
        for p in Path(self.c.get('sysfs_root', '/sys/bus/usb/devices')).iterdir():
            try:
                if (p / 'serial').read_text().strip() != self.c['target']['serial']:
                    continue
                if (p / 'product').read_text().strip() != self.c['target']['product']:
                    raise RecoveryError('USB product identity mismatch')
                bus, dev = int((p / 'busnum').read_text()), int((p / 'devnum').read_text())
                if not (1 <= bus <= 999 and 1 <= dev <= 999):
                    raise RecoveryError('Invalid USB node')
                found.append(str(Path(self.c.get('device_root', '/dev/bus/usb')) / f'{bus:03d}' / f'{dev:03d}'))
            except (FileNotFoundError, ValueError):
                continue
        if len(found) != 1:
            raise RecoveryError('Exact USB target absent or ambiguous')
        return found[0]

    def connect(self):
        node = self.usb()
        name = self.c['container_prefix']
        owner = 'org.ottplay.usb-supervisor=' + name
        # Only a container bearing our exact ownership label may be reused/replaced.
        current = self.docker('ps', '-a', '--filter', 'name=^/' + name + '$', '--format', '{{.Names}}')
        if current:
            details = json.loads(self.docker('inspect', name))[0]
            configuration = details.get('Config')
            labels = configuration.get('Labels') if isinstance(configuration, dict) else None
            if not isinstance(labels, dict) or labels.get('org.ottplay.usb-supervisor') != name:
                raise RecoveryError('Container name occupied by unrelated owner')
            host = details.get('HostConfig', {})
            devices = host.get('Devices', [])
            mounts = details.get('Mounts', [])
            mount_ok = (len(mounts) == 1 and mounts[0].get('Source') == self.c['adb_key_dir']
                        and mounts[0].get('Destination') == '/root/.android' and mounts[0].get('Type') == 'bind')
            good = (host.get('NetworkMode') == 'none' and not host.get('Privileged') and
                    any(d.get('PathOnHost') == node and d.get('PathInContainer') == node for d in devices)
                    and len(devices) == 1 and mount_ok and 'ALL' in (host.get('CapDrop') or [])
                    and not host.get('CapAdd') and not host.get('DeviceCgroupRules')
                    and not host.get('DeviceRequests') and not host.get('VolumesFrom')
                    and host.get('PidMode', '') in ('', 'private')
                    and host.get('IpcMode', '') in ('', 'private')
                    and any(v in ('no-new-privileges', 'no-new-privileges:true') for v in host.get('SecurityOpt', []))
                    and details.get('Config', {}).get('Image') == self.c['image'])
            if not good:
                self.docker('rm', '-f', name)
                current = ''
            elif not details.get('State', {}).get('Running'):
                self.docker('start', name)
        if not current:
            keys = Path(self.c['adb_key_dir'])
            if not keys.is_dir() or not (keys / 'adbkey').is_file():
                raise RecoveryError('Provision existing authorized ADB keys first')
            self.docker('run', '-d', '--name', name, '--label', owner, '--network', 'none',
                        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                        '--device', node + ':' + node,
                        '--mount', 'type=bind,src=' + str(keys) + ',dst=/root/.android',
                        self.c['image'], 'sleep', 'infinity')
        self.container, self.node = name, node
        self.verify()

    def adb(self, *args):
        if self.container is None:
            raise RecoveryError('Target not connected')
        return self.docker('exec', self.container, 'adb', '-s', self.c['target']['serial'], *args)

    def shell(self, *args):
        return self.adb('shell', *args)

    def verify(self):
        if self.adb('get-state') != 'device':
            raise RecoveryError('ADB target not authorized')
        t = self.c['target']
        for prop, want in [('ro.product.model', t['model']), ('ro.build.version.sdk', str(t['sdk']))]:
            if self.shell('getprop', prop) != want:
                raise RecoveryError('Android identity mismatch')
        if self.shell('settings', 'get', 'secure', 'android_id') != t['android_id']:
            raise RecoveryError('Android identity mismatch')

    def boot(self):
        value = self.shell('cat', '/proc/sys/kernel/random/boot_id')
        if not UUID.fullmatch(value):
            raise RecoveryError('Boot identity unavailable')
        return value

    def read_state(self):
        p = self.root / 'state.json'
        try:
            if p.is_symlink():
                raise RecoveryError('Recovery state must not be a symlink')
            state = json.loads(p.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise RecoveryError('Invalid or unreadable recovery state') from None
        if not isinstance(state, dict):
            raise RecoveryError('Recovery state must be an object')
        allowed = {'observed_boot', 'completed_boot', 'attempts', 'result'}
        if set(state) - allowed or 'observed_boot' not in state:
            raise RecoveryError('Unknown or incomplete recovery state fields')
        for key in ('observed_boot', 'completed_boot'):
            if key in state and (not isinstance(state[key], str) or not UUID.fullmatch(state[key])):
                raise RecoveryError('Invalid recovery boot identity')
        if 'attempts' in state and (type(state['attempts']) is not int or not 0 <= state['attempts'] <= 10):
            raise RecoveryError('Invalid recovery attempts')
        if 'result' in state and (
            not isinstance(state['result'], str) or not state['result']
            or any(ord(character) < 32 for character in state['result'])
        ):
            raise RecoveryError('Invalid recovery result')
        return state

    def save_state(self, state):
        save_recovery_state(self.root, state)

    def screenshot(self, name):
        def capture():
            self.connect()
            command = [
                self.c['docker_path'], 'exec', self.container, 'adb', '-s',
                self.c['target']['serial'], 'exec-out', 'screencap', '-p',
            ]
            result = subprocess.run(command, capture_output=True, timeout=30)
            if result.returncode:
                raise RecoveryError('Screenshot transport failed')
            return result.stdout
        return save_capture(self.root, name, capture)

    def locked(self):
        try:
            result = self.shell('am', 'broadcast', '--user', 'current', '-a', 'play.ott.usbprobe.KEYGUARD',
                                '-n', 'play.ott.usbprobe/.KeyguardReceiver')
            return probe_keyguard(result)
        except RecoveryError as exc:
            # A secure positive or malformed probe never falls through to weaker data.
            if 'returned no verified state' not in str(exc) and 'Transport operation failed' not in str(exc):
                raise
            return secure_keyguard(self.shell('dumpsys', 'window', 'policy'), self.shell('dumpsys', 'trust'))

    def wake(self):
        self.verify()
        self.shell('input', 'keyevent', '224')

    def current_display_size(self):
        policy = self.shell('dumpsys', 'window', 'policy')
        matches = re.findall(r'mUnrestrictedScreen=\(0,\s*0\)\s*(\d+)x(\d+)', policy)
        if matches:
            if len(set(matches)) != 1:
                raise RecoveryError('Ambiguous logical display geometry')
            return display_size('Override size: ' + 'x'.join(matches[0]))
        inputs = self.shell('dumpsys', 'input')
        frames = re.findall(r'logicalFrame=\[0,\s*0,\s*(\d+),\s*(\d+)\]', inputs)
        if frames:
            if len(set(frames)) != 1:
                raise RecoveryError('Ambiguous logical input geometry')
            return display_size('Override size: ' + 'x'.join(frames[0]))
        # The physical panel size is usable only with an observed current rotation.
        rotations = re.findall(r'(?:mRotation|SurfaceOrientation)\s*[:=]\s*([0-3])\b', policy + '\n' + inputs)
        if not rotations or len(set(rotations)) != 1:
            raise RecoveryError('Current display rotation unavailable')
        width, height = display_size(self.shell('wm', 'size'))
        return (height, width) if int(rotations[0]) % 2 else (width, height)

    def swipe(self, coordinates, duration=500):
        self.verify()
        self.locked()  # fail closed even for explicit manual swipe
        width, height = self.current_display_size()
        if len(coordinates) != 4 or any(type(v) is not int or v < 0 or v >= (width if i % 2 == 0 else height) for i, v in enumerate(coordinates)):
            raise RecoveryError('Swipe outside current display')
        if not 50 <= duration <= 3000:
            raise RecoveryError('Invalid swipe duration')
        self.shell('input', 'swipe', *map(str, coordinates), str(duration))

    def recover(self):
        self.read_state()  # A corrupt checkpoint must fail before wake or launch.
        self.verify()
        if self.shell('getprop', 'sys.boot_completed') != '1':
            raise RecoveryError('Android is still booting')
        boot = self.boot()
        locked = self.locked()
        self.wake()  # only after the security probe; explicit recovery/new boot only
        if locked:
            width, height = self.current_display_size()
            self.swipe([width // 2, height * 4 // 5, width // 2, height // 5])
            self.sleep(1)
        if self.locked():
            raise RecoveryError('Lockscreen remains; local unlock required')
        package = self.c.get('package', 'play.ott.foss')
        self.shell('monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1')
        self.sleep(2)
        activities = self.shell('dumpsys', 'activity', 'activities')
        if not re.search(r'(?:mResumedActivity|mFocusedActivity)[^\n]*\b' + re.escape(package) + r'/', activities):
            raise RecoveryError('OTT foreground not verified')
        if self.boot() != boot or self.locked():
            raise RecoveryError('Device state changed during recovery')
        state = self.read_state()
        state.update(completed_boot=boot, observed_boot=boot, attempts=0, result='recovered')
        self.save_state(state)
        return {'result': 'recovered', 'foreground': package}

    def tick(self):
        self.connect()
        boot, state = self.boot(), self.read_state()
        if not state.get('observed_boot'):
            self.save_state({'observed_boot': boot, 'completed_boot': boot, 'attempts': 0, 'result': 'armed_existing_boot'})
            return {'result': 'armed_existing_boot'}
        if state.get('completed_boot') == boot:
            return {'result': 'same_boot_no_action'}
        if self.shell('getprop', 'sys.boot_completed') != '1':
            return {'result': 'waiting_for_boot'}
        if state.get('observed_boot') != boot:
            state.update(observed_boot=boot, attempts=0)
        if state.get('attempts', 0) >= self.c.get('max_attempts', 3):
            return {'result': 'attempts_exhausted'}
        state['result'] = 'recovering'
        state['attempts'] = state.get('attempts', 0) + 1
        self.save_state(state)
        return self.recover()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('status', 'wake', 'recover', 'run', 'reboot'):
        sub.add_parser(cmd)
    swipe = sub.add_parser('swipe')
    swipe.add_argument('coordinates', type=int, nargs=4)
    swipe.add_argument('--duration', type=int, default=500)
    shot = sub.add_parser('screenshot')
    shot.add_argument('name', help='New bare PNG filename under state_dir/captures')
    args = parser.parse_args()
    if args.command == 'screenshot':
        validate_capture_name(args.name)
    supervisor = Supervisor(json.loads(Path(args.config).read_text()))
    lock = (supervisor.root / 'supervisor.lock').open('a')
    if args.command == 'run':
        previous_result = None
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = supervisor.tick()
            except (RecoveryError, BlockingIOError) as exc:
                result = {'result': 'unavailable', 'reason': str(exc)}
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            if result != previous_result:
                print(json.dumps(result), flush=True)
                previous_result = result
            supervisor.sleep(supervisor.c.get('poll_seconds', 5))
    # Waiting for ownership is safe: no device command runs before this lock.
    deadline = time.monotonic() + 30
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise RecoveryError('Target busy; no device action was sent') from None
            time.sleep(0.2)
    if args.command == 'screenshot':
        # Resolve the private destination before connecting or sending any ADB command.
        print(json.dumps(supervisor.screenshot(args.name)))
        return
    supervisor.connect()
    if args.command == 'status':
        result = {'connected': True, 'boot_completed': supervisor.shell('getprop', 'sys.boot_completed') == '1', 'locked': supervisor.locked(), 'state': supervisor.read_state()}
    elif args.command == 'recover':
        result = supervisor.recover()
    elif args.command == 'wake':
        supervisor.wake()
        result = {'result': 'wake_sent'}
    elif args.command == 'swipe':
        supervisor.swipe(args.coordinates, args.duration)
        result = {'result': 'swipe_sent'}
    elif args.command == 'reboot':
        supervisor.adb('reboot')
        result = {'result': 'reboot_sent'}
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (RecoveryError, OSError, ValueError) as exc:
        raise SystemExit(str(exc))
