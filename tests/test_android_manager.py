"""USB identity, transport and recovery boundaries; no physical devices needed."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1] / 'deploy/android-manager'
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('android_manager', ROOT / 'manager.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
sys.path.pop(0)


def config():
    return {'version': 1, 'key_names': ['fire', 'a1'], 'devices': {'tablet': {
        'serial': 'SERIAL', 'product': 'Fire', 'vid': '1949', 'pid': '0232',
        'model': 'MODEL', 'sdk': 22, 'identity_property': 'android_id',
        'identity_value': '0123456789abcdef', 'auto_recover': True, 'forward_port': 9225}}}


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = config()
        self.commands = []

    def read(self):
        path = self.root / 'config.json'
        path.write_text(json.dumps(self.config))
        return m.config_read(path)

    def node(self, name='1-1', serial='SERIAL', model='Fire', vid='1949'):
        path = self.root / name
        path.mkdir(exist_ok=True)
        for key, value in {'serial': serial, 'product': model, 'idVendor': vid,
                           'idProduct': '0232', 'busnum': '1', 'devnum': '6'}.items():
            (path / key).write_text(value)

    def runner(self, args):
        self.commands.append(args)
        if args == ['adb', 'devices', '-l']:
            return b'List of devices attached\nSERIAL device usb:1-1 model:MODEL transport_id:4\n'
        self.assertEqual(args[:3], ['adb', '-t', '4'])
        values = {'ro.product.model': b'MODEL', 'ro.build.version.sdk': b'22',
                  'android_id': b'0123456789abcdef'}
        return values.get(args[-1], b'device')

    def test_missing_keys_and_duplicate_registry_refused(self):
        self.assertEqual(self.read(), self.config)
        self.config['key_names'] = ['fire']
        with self.assertRaises(m.ManagerError): self.read()
        self.config = config()
        self.config['devices']['other'] = dict(self.config['devices']['tablet'])
        with self.assertRaises(m.ManagerError): self.read()

    def test_only_verified_physical_nodes_mapped(self):
        self.node()
        self.node('1-2', serial='UNKNOWN')
        self.assertEqual(m.usb_inventory(self.config, self.root), {'tablet': '/dev/bus/usb/001/006'})

    def test_duplicate_serial_including_other_model_excluded(self):
        self.node()
        self.node('1-2', model='OTHER', vid='0000')
        self.assertEqual(m.usb_inventory(self.config, self.root), {})

    def test_wrong_vid_excluded(self):
        self.node(vid='0000')
        self.assertEqual(m.usb_inventory(self.config, self.root), {})

    def test_transport_is_verified_before_device_command(self):
        target = m.Target(self.config['devices']['tablet'], self.runner)
        target.verify()
        self.assertEqual(target.adb('get-state'), b'device')
        self.assertEqual(len(self.commands), 5)
        self.assertTrue(all('-s' not in command for command in self.commands))

    def test_wrong_android_identity_refused(self):
        def runner(args):
            return b'WRONG' if args[-1] == 'android_id' else self.runner(args)
        with self.assertRaises(m.ManagerError): m.Target(self.config['devices']['tablet'], runner).verify()

    def test_ambiguous_or_non_usb_transport_refused_without_probe(self):
        for value in [b'SERIAL device usb:1-1 transport_id:4\nSERIAL device usb:1-2 transport_id:5',
                      b'SERIAL unauthorized usb:1-1 transport_id:4', b'SERIAL device transport_id:4',
                      b'SERIAL', b'SERIAL device usb:1-1 transport_id:x']:
            calls = []
            def runner(args):
                calls.append(args)
                return value
            with self.assertRaises(m.ManagerError): m.Target(self.config['devices']['tablet'], runner).verify()
            self.assertEqual(len(calls), 1)

    def test_no_global_server_mutations_or_cross_device_forwarding(self):
        target = self.config['devices']['tablet']
        for command in [['kill-server'], ['start-server'], ['reconnect'], ['tcpip', '5555'],
                        ['forward', '--remove-all'], ['forward', 'tcp:9222', 'localabstract:x']]:
            with self.assertRaises(m.ManagerError): m.safe_args(command, target)
        self.assertEqual(m.safe_args(['forward', 'tcp:9225', 'localabstract:x'], target)[0], 'forward')

    @unittest.skipIf(sys.platform == 'win32', 'flock runtime is Linux-only')
    def test_unknown_alias_and_busy_manager_send_no_command(self):
        with self.assertRaises(m.ManagerError):
            m.command(self.config, self.root, 'unknown', ['get-state'], self.runner)
        with m.lease(self.root):
            with self.assertRaises(m.ManagerError):
                with m.lease(self.root, wait=0): pass
        self.assertEqual(self.commands, [])

    @unittest.skipIf(sys.platform == 'win32', 'flock runtime is Linux-only')
    def test_unrelated_container_is_never_removed(self):
        self.config.update(docker_path='docker', image='approved@sha256:'+'a'*64,
                           key_dir=str(self.root/'keys'), state_dir=str(self.root/'state'))
        path = self.root/'config.json'; path.write_text(json.dumps(self.config))
        calls=[]
        def run(args, **kwargs):
            calls.append(args)
            if args[1] == 'ps': return b'android-manager'
            return json.dumps([{'Config':{'Labels':{}}}]).encode()
        with patch.object(m, 'usb_inventory', return_value={}), patch.object(m, 'run', side_effect=run):
            with self.assertRaises(m.ManagerError): m.reconcile(self.config, path)
        self.assertFalse(any('rm' in c or 'stop' in c for c in calls))

    def test_dsm_without_pids_controller_matches_without_recreate_loop(self):
        spec={'image':'image@sha256:abc','keys':'/keys','state':'/state','config':'/config','nodes':{'tablet':'/dev/bus/usb/001/099'}}
        details={'Config':{'Image':spec['image'],'User':'0:0','Entrypoint':['python3','/app/manager.py'],'Cmd':['--config','/config.json','--state-root','/state','serve']},
                 'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'CpuShares':128,'Memory':268435456,'MemorySwap':268435456,'PidsLimit':None,'RestartPolicy':{'Name':'no'},'Tmpfs':{'/tmp':'rw,nosuid,nodev,size=134217728,mode=1777'},'Devices':[{'PathOnHost':'/dev/bus/usb/001/099','PathInContainer':'/dev/bus/usb/001/099','CgroupPermissions':'rw'}]},
                 'Mounts':[{'Type':'bind','Source':spec[key],'Destination':dest,'RW':rw} for key,dest,rw in [('keys','/keys',False),('state','/state',True),('config','/config.json',False)]]}
        self.assertTrue(m.runtime_matches(details,spec))
        details['HostConfig']['NetworkMode']='host'
        self.assertFalse(m.runtime_matches(details,spec))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('fire', 'a1'):
            key = self.root/'keys'/name
            key.mkdir(parents=True)
            (key/'adbkey').write_text('existing-private-key')
            (key/'adbkey.pub').write_text('existing-public-key')
        self.env = patch.dict(m.os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = config()
        self.config['devices']['tablet']['auto_recover'] = False

    def test_child_failure_exits_and_reaps_child(self):
        from unittest.mock import Mock
        child = Mock()
        child.poll.return_value = 1
        with patch.object(m.subprocess, 'Popen', return_value=child), patch.object(m.signal, 'signal'):
            with self.assertRaises(m.ManagerError):
                m.serve(self.config, self.root, self.root/'keys', self.root/'home')
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=10)

    def test_sigterm_is_bounded_and_does_not_send_device_commands(self):
        from unittest.mock import Mock
        child = Mock()
        child.poll.return_value = None
        handlers = {}
        def register(sig, fn): handlers[sig] = fn
        def sleep(_seconds): handlers[m.signal.SIGTERM](m.signal.SIGTERM, None)
        with patch.object(m.subprocess, 'Popen', return_value=child), patch.object(m.signal, 'signal', side_effect=register), patch.object(m.time, 'sleep', side_effect=sleep), patch.object(m, 'run', side_effect=AssertionError('no ADB commands expected')):
            m.serve(self.config, self.root, self.root/'keys', self.root/'home')
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=10)

    def test_missing_original_key_cannot_generate_new_identity(self):
        (self.root/'keys/a1/adbkey').unlink()
        with patch.object(m.subprocess, 'Popen') as child:
            with self.assertRaises(m.ManagerError):
                m.serve(self.config, self.root, self.root/'keys', self.root/'home')
        child.assert_not_called()

    @unittest.skipIf(sys.platform == 'win32', 'flock runtime is Linux-only')
    def test_hotplug_recreates_only_owned_name_with_exact_new_nodes(self):
        self.config.update(docker_path='docker', image='image@sha256:'+'a'*64, key_dir=str(self.root/'keys'), state_dir=str(self.root/'state'))
        path = self.root/'config.json'; path.write_text(json.dumps(self.config))
        calls=[]
        def run(args, **kwargs):
            calls.append(args)
            if args[1] == 'ps': return b'android-manager'
            if args[1] == 'inspect': return json.dumps([{'Config':{'Labels':{'org.ottplay.android-manager':'1','org.ottplay.android-manager.spec':'old'}}}]).encode()
            return b''
        with patch.object(m, 'usb_inventory', return_value={'tablet':'/dev/bus/usb/001/099'}), patch.object(m, 'run', side_effect=run):
            m.reconcile(self.config, path)
        self.assertIn(['docker','stop','-t','15','android-manager'], calls)
        self.assertIn(['docker','rm','android-manager'], calls)
        create=next(c for c in calls if c[1]=='run')
        self.assertEqual([create[i+1] for i,x in enumerate(create) if x=='--device'], ['/dev/bus/usb/001/099:/dev/bus/usb/001/099:rw'])
        self.assertNotIn('--privileged', create)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        target = m.Target(config()['devices']['tablet'])
        self.recovery = m.Recovery(target, self.temp.name, sleep=lambda _: None)
        self.recovery.connect = lambda: None
        self.recovery.verify = lambda: None
        self.recovery.boot = lambda: 'boot-A'

    def test_preserved_current_boot_and_initial_arm_never_wake(self):
        self.recovery.recover = lambda: self.fail('must not recover the current boot')
        self.assertEqual(self.recovery.tick()['result'], 'armed_existing_boot')
        self.assertEqual(self.recovery.tick()['result'], 'same_boot_no_action')
        self.recovery.shell = lambda *args: self.fail('must not wake a sleeping device')
        self.assertEqual(self.recovery.tick()['result'], 'same_boot_no_action')

    def test_new_boot_waits_and_attempts_are_bounded(self):
        self.recovery.save_state({'observed_boot':'old','completed_boot':'old'})
        self.recovery.shell = lambda *args: '0'
        self.assertEqual(self.recovery.tick()['result'], 'waiting_for_boot')
        self.recovery.shell = lambda *args: '1'
        def fail(): raise m.RecoveryError('secure')
        self.recovery.recover = fail
        for _ in range(3):
            with self.assertRaises(m.RecoveryError): self.recovery.tick()
        self.assertEqual(self.recovery.tick()['result'], 'attempts_exhausted')

    def test_secure_or_unknown_lock_never_wakes(self):
        self.recovery.shell = lambda *args: '1'
        def locked(): raise m.RecoveryError('secure')
        self.recovery.locked = locked
        self.recovery.wake = lambda: self.fail('must not wake secure device')
        with self.assertRaises(m.RecoveryError): self.recovery.recover()
        for value in ['', 'secure=true', 'secure=false secure=true']:
            with self.assertRaises(m.RecoveryError):
                sys.modules['recovery'].secure_keyguard('mShowingLockscreen=true', value)

    def test_completion_requires_foreground_and_same_boot(self):
        self.recovery.locked = lambda: False
        self.recovery.wake = lambda: None
        self.recovery.shell = lambda *args: '1' if args[0] == 'getprop' else 'not OTT'
        with self.assertRaises(m.RecoveryError): self.recovery.recover()
        self.assertNotIn('completed_boot', self.recovery.read_state())
        self.recovery.shell = lambda *args: '1' if args[0] == 'getprop' else 'mResumedActivity: play.ott.foss/.MainActivity'
        self.assertEqual(self.recovery.recover()['result'], 'recovered')


if __name__ == '__main__':
    unittest.main()
