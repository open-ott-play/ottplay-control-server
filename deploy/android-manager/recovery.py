#!/usr/bin/env python3
"""Explicit-target USB Android recovery. No network ADB or general shell API."""
import json
import os
from pathlib import Path
import re
import time


class RecoveryError(RuntimeError):
    pass


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


class Recovery:
    def __init__(self, target, root, sleep=time.sleep):
        self.target = target
        self.c = target.config
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sleep = sleep

    def connect(self):
        self.target.verify()

    def verify(self):
        self.target.verify()

    def shell(self, *args):
        return self.target.adb('shell', *args).decode(errors='replace').strip()

    def boot(self):
        value = self.shell('cat', '/proc/sys/kernel/random/boot_id')
        if not re.fullmatch('[0-9a-f-]{36}', value):
            raise RecoveryError('Boot identity unavailable')
        return value

    def read_state(self):
        p = self.root / 'state.json'
        return json.loads(p.read_text()) if p.exists() else {}

    def save_state(self, state):
        p = self.root / 'state.tmp'
        with p.open('w') as f:
            os.chmod(p, 0o600)
            json.dump(state, f)
        p.replace(self.root / 'state.json')

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

