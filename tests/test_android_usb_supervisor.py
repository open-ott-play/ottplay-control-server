import importlib.util
import json
import os
from pathlib import Path
import runpy
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

if os.name != 'posix':
    raise unittest.SkipTest('USB host supervision requires POSIX file locks')

SPEC = importlib.util.spec_from_file_location('usb_supervisor', Path(__file__).parents[1] / 'tools/android_usb_supervisor.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

BOOT_A = '00000000-0000-0000-0000-000000000001'
BOOT_B = '00000000-0000-0000-0000-000000000002'
PNG = b'\x89PNG\r\n\x1a\nsynthetic screenshot'


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'version': 1, 'target': {'serial':'SERIAL', 'product':'Fire', 'model':'KFGIWI', 'sdk':22, 'android_id':'0123456789abcdef'}, 'docker_path':'docker', 'image':'approved:image', 'adb_key_dir':str(self.root / 'keys'), 'container_prefix':'owned-target', 'state_dir':str(self.root / 'state'), 'sysfs_root':str(self.root / 'usb')}
        (self.root / 'usb').mkdir()
        (self.root / 'keys').mkdir()
        (self.root / 'keys/adbkey').write_text('private')
        self.commands = []
        self.s = mod.Supervisor(self.config, runner=self.run_command, sleep=lambda _: None)

    def run_command(self, argv):
        self.commands.append(argv)
        return ''

    def usb_node(self, name='1-1', serial='SERIAL', dev='3'):
        p = self.root / 'usb' / name
        p.mkdir(exist_ok=True)
        for k, v in {'serial':serial,'product':'Fire','busnum':'1','devnum':dev}.items():
            (p / k).write_text(v)
        return p

    def test_unique_identity_required(self):
        self.usb_node()
        self.assertEqual(self.s.usb(), '/dev/bus/usb/001/003')
        self.usb_node('1-2')
        with self.assertRaises(mod.RecoveryError): self.s.usb()

    def test_reenumeration_replaces_only_owned_container(self):
        self.usb_node(dev='5')
        def runner(argv):
            self.commands.append(argv)
            if argv[1] == 'ps': return 'owned-target'
            if argv[1] == 'inspect': return json.dumps([{'Config':{'Labels':{'org.ottplay.usb-supervisor':'owned-target'},'Image':'approved:image'}, 'HostConfig':{'NetworkMode':'none','Devices':[{'PathOnHost':'/dev/bus/usb/001/003','PathInContainer':'/dev/bus/usb/001/003'}]},'Mounts':[], 'State':{'Running':True}}])
            return ''
        self.s.runner = runner
        self.s.verify = lambda: None
        self.s.connect()
        self.assertIn(['docker','rm','-f','owned-target'], self.commands)
        run = next(x for x in self.commands if x[1] == 'run')
        self.assertIn('/dev/bus/usb/001/005:/dev/bus/usb/001/005', run)
        self.assertIn('none', run)
        self.assertIn('no-new-privileges', run)

    def test_refuse_unrelated_container(self):
        self.usb_node()
        def runner(argv):
            self.commands.append(argv)
            return 'owned-target' if argv[1] == 'ps' else json.dumps([{'Config':{'Labels':{}}}])
        self.s.runner = runner
        with self.assertRaises(mod.RecoveryError): self.s.connect()
        self.assertEqual([a[1] for a in self.commands], ['ps', 'inspect'])
        self.assertFalse(any(a[1] == 'rm' for a in self.commands))

    def test_unlabelled_container_is_refused_without_changing_it(self):
        self.usb_node()
        def runner(argv):
            self.commands.append(argv)
            return 'owned-target' if argv[1] == 'ps' else json.dumps([{'Config': {'Labels': None}}])
        self.s.runner = runner
        with self.assertRaisesRegex(mod.RecoveryError, 'unrelated owner'):
            self.s.connect()
        self.assertEqual([command[1] for command in self.commands], ['ps', 'inspect'])

    def test_matching_container_is_reused_without_recreation(self):
        self.usb_node()
        details = {
            'Config': {
                'Labels': {'org.ottplay.usb-supervisor': 'owned-target'},
                'Image': 'approved:image',
            },
            'HostConfig': {
                'NetworkMode': 'none',
                'Devices': [{
                    'PathOnHost': '/dev/bus/usb/001/003',
                    'PathInContainer': '/dev/bus/usb/001/003',
                }],
                'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                'IpcMode': 'private',
            },
            'Mounts': [{
                'Source': self.config['adb_key_dir'], 'Destination': '/root/.android',
                'Type': 'bind',
            }],
            'State': {'Running': True},
        }
        def runner(argv):
            self.commands.append(argv)
            return 'owned-target' if argv[1] == 'ps' else json.dumps([details])
        self.s.runner = runner
        with mock.patch.object(self.s, 'verify') as verify:
            self.s.connect()
        self.assertEqual([a[1] for a in self.commands], ['ps', 'inspect'])
        verify.assert_called_once_with()

    def test_secure_unknown_conflicting_refused(self):
        for trust in ['', 'secure=true', 'secure=false secure=true']:
            with self.assertRaises(mod.RecoveryError): mod.secure_keyguard('mShowingLockscreen=true', trust)
        self.assertTrue(mod.secure_keyguard('mShowingLockscreen=true', 'deviceSecure=false'))
        self.assertFalse(mod.secure_keyguard('mShowingLockscreen=false', 'deviceSecure=false'))

    def test_probe_requires_exact_boolean_security(self):
        self.assertTrue(mod.probe_keyguard('Broadcast completed: result=0, data="{"secure":false,"locked":true}"'))
        self.assertFalse(mod.probe_keyguard('Broadcast completed: result=0, data={"secure":false,"locked":false}'))
        for raw in ['{"secure":true,"locked":false}', '{"locked":false}', '{"secure":"false","locked":true}', '{}']:
            with self.assertRaises(mod.RecoveryError):
                mod.probe_keyguard('Broadcast completed: result=0, data=' + raw)

    def test_boot_identity_uses_the_same_format_as_persisted_checkpoints(self):
        for value in ('a' * 36, '-' * 36, BOOT_A):
            with self.subTest(value=value), mock.patch.object(self.s, 'shell', return_value=value):
                if value == BOOT_A:
                    self.assertEqual(self.s.boot(), BOOT_A)
                else:
                    with self.assertRaises(mod.RecoveryError):
                        self.s.boot()

    def test_initial_arm_does_not_unlock(self):
        self.s.connect = lambda: None
        self.s.boot = lambda: BOOT_A
        self.s.recover = lambda: self.fail('must not recover existing boot')
        self.assertEqual(self.s.tick()['result'], 'armed_existing_boot')
        self.assertEqual(self.s.tick()['result'], 'same_boot_no_action')

    def test_sleep_on_completed_boot_never_wakes(self):
        self.s.save_state({'observed_boot':BOOT_A,'completed_boot':BOOT_A})
        self.s.connect = lambda: None
        self.s.boot = lambda: BOOT_A
        self.s.shell = lambda *a: self.fail('same boot must not execute shell action')
        self.s.recover = lambda: self.fail('same boot must not recover')
        self.assertEqual(self.s.tick()['result'], 'same_boot_no_action')

    def test_retry_limit_and_wait_for_boot(self):
        self.s.save_state({'observed_boot':BOOT_A,'completed_boot':BOOT_A})
        self.s.connect = lambda: None
        self.s.boot = lambda: BOOT_B
        self.s.shell = lambda *a: '0'
        self.assertEqual(self.s.tick()['result'], 'waiting_for_boot')
        self.s.shell = lambda *a: '1'
        def fail(): raise mod.RecoveryError('not yet')
        self.s.recover = fail
        for _ in range(3):
            with self.assertRaises(mod.RecoveryError): self.s.tick()
        self.assertEqual(self.s.tick()['result'], 'attempts_exhausted')
        self.assertEqual(self.s.read_state()['completed_boot'], BOOT_A)

    def test_success_marker_only_after_verification(self):
        self.s.verify = lambda: None
        self.s.boot = lambda: '00000000-0000-0000-0000-000000000001'
        self.s.locked = lambda: False
        self.s.wake = lambda: None
        self.s.shell = lambda *a: '1' if a[0] == 'getprop' else 'not OTT'
        with self.assertRaises(mod.RecoveryError): self.s.recover()
        self.assertNotIn('completed_boot', self.s.read_state())
        self.s.shell = lambda *a: '1' if a[0] == 'getprop' else 'mResumedActivity: ActivityRecord{ play.ott.foss/.MainActivity }'
        self.assertEqual(self.s.recover()['result'], 'recovered')
        self.assertEqual(self.s.read_state()['completed_boot'], self.s.boot())

    def test_secure_recovery_never_wakes(self):
        self.s.verify = lambda: None
        self.s.shell = lambda *a: '1'
        self.s.boot = lambda: BOOT_A
        def refuse(): raise mod.RecoveryError('secure')
        self.s.locked = refuse
        self.s.wake = lambda: self.fail('must not wake secure device')
        with self.assertRaises(mod.RecoveryError): self.s.recover()

    def test_logical_landscape_overrides_portrait_panel(self):
        self.s.shell = lambda *a: 'mUnrestrictedScreen=(0,0) 1280x800' if a == ('dumpsys', 'window', 'policy') else 'Physical size: 800x1280'
        self.assertEqual(self.s.current_display_size(), (1280, 800))
        self.s.shell = lambda *a: 'SurfaceOrientation: 1' if a == ('dumpsys', 'input') else ('Physical size: 800x1280' if a == ('wm', 'size') else '')
        self.assertEqual(self.s.current_display_size(), (1280, 800))

    def test_logical_input_frame(self):
        self.s.shell = lambda *a: 'logicalFrame=[0, 0, 1280, 800]' if a == ('dumpsys', 'input') else ''
        self.assertEqual(self.s.current_display_size(), (1280, 800))

    def test_explicit_recover_wakes_unlocked_screen(self):
        self.s.verify = lambda: None
        self.s.boot = lambda: BOOT_A
        self.s.locked = lambda: False
        wakes = []
        self.s.wake = lambda: wakes.append(True)
        self.s.shell = lambda *a: '1' if a[0] == 'getprop' else 'mResumedActivity: play.ott.foss/.MainActivity'
        self.s.recover()
        self.assertEqual(wakes, [True])

    def test_container_extra_access_requires_rebuild(self):
        self.usb_node()
        base = {'Config':{'Labels':{'org.ottplay.usb-supervisor':'owned-target'},'Image':'approved:image'}, 'HostConfig':{'NetworkMode':'none','Devices':[{'PathOnHost':'/dev/bus/usb/001/003','PathInContainer':'/dev/bus/usb/001/003'}],'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'IpcMode':'private'}, 'Mounts':[{'Source':self.config['adb_key_dir'],'Destination':'/root/.android','Type':'bind'}], 'State':{'Running':True}}
        for field, value in [('CapAdd',['SYS_ADMIN']),('DeviceCgroupRules',['c *:* rwm']),('PidMode','host'),('IpcMode','host')]:
            details = json.loads(json.dumps(base))
            details['HostConfig'][field] = value
            self.commands.clear()
            def runner(argv):
                self.commands.append(argv)
                return 'owned-target' if argv[1] == 'ps' else (json.dumps([details]) if argv[1] == 'inspect' else '')
            self.s.runner = runner
            self.s.verify = lambda: None
            self.s.connect()
            self.assertIn(['docker','rm','-f','owned-target'], self.commands)

    def test_swipe_dimensions(self):
        self.s.verify = lambda: None
        self.s.locked = lambda: False
        self.s.current_display_size = lambda: (800, 1280)
        with self.assertRaises(mod.RecoveryError): self.s.swipe([400,1000,400,1280])
        with self.assertRaises(mod.RecoveryError): self.s.swipe([400,1000,400,100], 1)

    def test_malformed_state_blocks_recovery_before_device_commands(self):
        path = self.s.root / 'state.json'
        for contents in ('{PRIVATE_SENTINEL', '[]', 'null', '"PRIVATE_SENTINEL"', '1'):
            with self.subTest(contents=contents):
                path.write_text(contents)
                with self.assertRaises(mod.RecoveryError) as failure:
                    self.s.recover()
                self.assertNotIn('PRIVATE_SENTINEL', str(failure.exception))
                self.assertEqual(self.commands, [])
                self.assertEqual(path.read_text(), contents)

    def test_invalid_checkpoint_fields_refuse_tick_and_recovery(self):
        path = self.s.root / 'state.json'
        invalid_states = [
            {},
            {'completed_boot': BOOT_A},
            {'observed_boot': 'a' * 36},
            {'observed_boot': BOOT_B, 'completed_boot': 1},
            *[
                {'observed_boot': BOOT_B, 'completed_boot': BOOT_A, 'attempts': attempts}
                for attempts in ('1', None, True, -1, 11, 1.5)
            ],
            {'observed_boot': BOOT_B, 'result': ''},
            {'observed_boot': BOOT_B, 'result': 'private\nvalue'},
            {'observed_boot': BOOT_B, 'extra': True},
        ]
        for state in invalid_states:
            for action in ('tick', 'recover'):
                with self.subTest(state=state, action=action):
                    contents = json.dumps(state)
                    path.write_text(contents)
                    with mock.patch.object(self.s, 'connect'), \
                            mock.patch.object(self.s, 'boot', return_value=BOOT_B), \
                            mock.patch.object(self.s, 'shell') as shell:
                        with self.assertRaises(mod.RecoveryError):
                            getattr(self.s, action)()
                    shell.assert_not_called()
                    self.assertEqual(path.read_text(), contents)

    def test_legacy_checkpoint_formats_keep_their_existing_boot_policy(self):
        for state in (
            {'observed_boot': BOOT_A, 'completed_boot': BOOT_A},
            {'observed_boot': BOOT_A, 'completed_boot': BOOT_A, 'attempts': 0,
             'result': 'armed_existing_boot'},
            {'observed_boot': BOOT_A, 'completed_boot': BOOT_A, 'attempts': 0,
             'result': 'recovered'},
        ):
            with self.subTest(state=state):
                self.s.save_state(state)
                with mock.patch.object(self.s, 'connect'), \
                        mock.patch.object(self.s, 'boot', return_value=BOOT_A), \
                        mock.patch.object(self.s, 'shell') as shell:
                    self.assertEqual(self.s.tick(), {'result': 'same_boot_no_action'})
                shell.assert_not_called()
                self.assertEqual(self.s.read_state(), state)

    def test_state_symlinks_and_unreadable_files_are_not_missing_state(self):
        path = self.s.root / 'state.json'
        target = self.root / 'outside-state.json'
        for exists in (False, True):
            with self.subTest(target_exists=exists):
                if exists:
                    target.write_text('{}')
                path.symlink_to(target)
                with self.assertRaises(mod.RecoveryError):
                    self.s.read_state()
                path.unlink()
        path.write_text('{}')
        with mock.patch.object(Path, 'read_text', side_effect=PermissionError('PRIVATE_SENTINEL')):
            with self.assertRaises(mod.RecoveryError) as failure:
                self.s.read_state()
        self.assertNotIn('PRIVATE_SENTINEL', str(failure.exception))
        path.unlink()
        self.assertEqual(self.s.read_state(), {})

    def test_state_persistence_flushes_file_before_replace_then_directory(self):
        state = {'observed_boot': BOOT_A, 'completed_boot': BOOT_A, 'attempts': 0}
        events = []
        real_fsync, real_replace = os.fsync, os.replace
        def sync(fd):
            events.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
            return real_fsync(fd)
        def replace(*args, **kwargs):
            events.append('replace')
            return real_replace(*args, **kwargs)
        with mock.patch.object(mod.os, 'fsync', side_effect=sync):
            with mock.patch.object(mod.os, 'replace', side_effect=replace):
                self.s.save_state(state)
        self.assertEqual(events, ['file', 'replace', 'directory'])
        self.assertEqual(self.s.read_state(), state)
        path = self.s.root / 'state.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.s.root.iterdir()], ['state.json'])

    def test_state_write_and_replace_failures_preserve_previous_checkpoint(self):
        old = {'observed_boot': BOOT_A, 'completed_boot': BOOT_A}
        new = {'observed_boot': BOOT_B, 'completed_boot': BOOT_B}
        self.s.save_state(old)
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                with mock.patch.object(mod.os, operation, side_effect=OSError('PRIVATE_SENTINEL')):
                    with self.assertRaises(mod.RecoveryError) as failure:
                        self.s.save_state(new)
                self.assertNotIn('PRIVATE_SENTINEL', str(failure.exception))
                self.assertEqual(self.s.read_state(), old)
                self.assertEqual([p.name for p in self.s.root.iterdir()], ['state.json'])

    def test_directory_sync_failure_is_reported_after_atomic_replace(self):
        self.s.save_state({'observed_boot': BOOT_A})
        real_fsync = os.fsync
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('directory sync failed')
            return real_fsync(fd)
        with mock.patch.object(mod.os, 'fsync', side_effect=sync):
            with self.assertRaises(mod.RecoveryError):
                self.s.save_state({'observed_boot': BOOT_B})
        self.assertEqual(self.s.read_state(), {'observed_boot': BOOT_B})
        self.assertEqual([p.name for p in self.s.root.iterdir()], ['state.json'])

    def test_capture_reserves_private_destination_before_callback(self):
        path = self.s.root / 'captures' / 'screen.png'
        def capture():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b'')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            return PNG
        self.assertEqual(
            mod.save_capture(self.s.root, 'screen.png', capture),
            {'result': 'captured', 'path': str(path)},
        )
        self.assertEqual(path.read_bytes(), PNG)

    def test_legacy_screenshot_rejects_paths_before_device_connection(self):
        for name in ('/tmp/root.png', '../root.png', r'..\root.png', 'nested/root.png'):
            with self.subTest(name=name), mock.patch.object(self.s, 'connect') as connect:
                with mock.patch.object(mod.subprocess, 'run') as run:
                    with self.assertRaises(mod.RecoveryError):
                        self.s.screenshot(name)
                connect.assert_not_called()
                run.assert_not_called()
        self.assertEqual(self.commands, [])

    def test_legacy_cli_rejects_screenshot_path_before_reading_config(self):
        with mock.patch.object(sys, 'argv', [
            'android_usb_supervisor.py', '--config', '/must-not-read', 'screenshot', '/tmp/root.png',
        ]):
            with mock.patch.object(Path, 'read_text') as read:
                with self.assertRaises(mod.RecoveryError):
                    mod.main()
            read.assert_not_called()

    def test_legacy_screenshot_existing_destination_or_symlink_never_connects(self):
        captures = self.s.root / 'captures'
        captures.mkdir()
        destination = captures / 'screen.png'
        outside = self.root / 'outside.png'
        outside.write_bytes(b'preserve')
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    destination.symlink_to(outside)
                else:
                    destination.write_bytes(b'preserve')
                with mock.patch.object(self.s, 'connect') as connect:
                    with self.assertRaises(mod.RecoveryError):
                        self.s.screenshot('screen.png')
                    connect.assert_not_called()
                self.assertEqual(destination.read_bytes(), b'preserve')
                self.assertEqual(outside.read_bytes(), b'preserve')
                destination.unlink()

    def test_legacy_screenshot_timeout_and_os_error_clean_reservation(self):
        path = self.s.root / 'captures' / 'screen.png'
        self.s.container = 'owned-target'
        for failure in (
            subprocess.TimeoutExpired(['private-command'], 30),
            OSError('PRIVATE_SENTINEL'),
        ):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(self.s, 'connect') as connect:
                    with mock.patch.object(mod.subprocess, 'run', side_effect=failure) as run:
                        with self.assertRaises(mod.RecoveryError) as caught:
                            self.s.screenshot('screen.png')
                    connect.assert_called_once_with()
                    run.assert_called_once()
                    self.assertEqual(run.call_args.kwargs['timeout'], 30)
                self.assertNotIn('PRIVATE_SENTINEL', str(caught.exception))
                self.assertFalse(path.exists())

    def test_capture_sync_error_cleans_file_and_does_not_report_success(self):
        captures = self.s.root / 'captures'
        for fail_directory in (False, True):
            with self.subTest(fail_directory=fail_directory):
                real_fsync = os.fsync
                def sync(fd):
                    if stat.S_ISDIR(os.fstat(fd).st_mode) == fail_directory:
                        raise OSError('fsync failure')
                    return real_fsync(fd)
                with mock.patch.object(mod.os, 'fsync', side_effect=sync):
                    with self.assertRaises(mod.RecoveryError):
                        mod.save_capture(self.s.root, 'screen.png', lambda: PNG)
                self.assertEqual(list(captures.iterdir()), [])


class LegacyRemoteTests(unittest.TestCase):
    script = Path(__file__).parents[1] / 'tools/android_usb_remote.py'

    def test_ssh_connection_is_bounded_and_result_code_is_preserved(self):
        with mock.patch.object(sys, 'argv', [
            str(self.script), '--host', 'nas', 'screenshot', 'screen.png',
        ]):
            with mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 23)) as run:
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(self.script), run_name='__main__')
        self.assertEqual(stopped.exception.code, 23)
        command = run.call_args.args[0]
        self.assertEqual(command[:-1], [
            'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '--', 'nas',
        ])
        self.assertEqual(shlex.split(command[-1])[-2:], ['screenshot', 'screen.png'])

    def test_bad_screenshot_path_never_launches_ssh(self):
        for name in ('/tmp/screen.png', '../screen.png', 'a/b.png', 'screen.PNG'):
            with self.subTest(name=name):
                with mock.patch.object(sys, 'argv', [
                    str(self.script), '--host', 'nas', 'screenshot', name,
                ]), mock.patch.object(sys, 'stderr'):
                    with mock.patch.object(subprocess, 'run') as run:
                        with self.assertRaises(SystemExit) as stopped:
                            runpy.run_path(str(self.script), run_name='__main__')
                self.assertEqual(stopped.exception.code, 2)
                run.assert_not_called()


if __name__ == '__main__': unittest.main()
