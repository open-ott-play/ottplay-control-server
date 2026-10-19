import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('usb_supervisor', Path(__file__).parents[1] / 'tools/android_usb_supervisor.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


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
        self.s.runner = lambda a: 'owned-target' if a[1] == 'ps' else json.dumps([{'Config':{'Labels':{}}}])
        with self.assertRaises(mod.RecoveryError): self.s.connect()
        self.assertFalse(any(a[1] == 'rm' for a in self.commands))

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

    def test_initial_arm_does_not_unlock(self):
        self.s.connect = lambda: None
        self.s.boot = lambda: 'boot-A'
        self.s.recover = lambda: self.fail('must not recover existing boot')
        self.assertEqual(self.s.tick()['result'], 'armed_existing_boot')
        self.assertEqual(self.s.tick()['result'], 'same_boot_no_action')

    def test_sleep_on_completed_boot_never_wakes(self):
        self.s.save_state({'observed_boot':'boot-A','completed_boot':'boot-A'})
        self.s.connect = lambda: None
        self.s.boot = lambda: 'boot-A'
        self.s.shell = lambda *a: self.fail('same boot must not execute shell action')
        self.s.recover = lambda: self.fail('same boot must not recover')
        self.assertEqual(self.s.tick()['result'], 'same_boot_no_action')

    def test_retry_limit_and_wait_for_boot(self):
        self.s.save_state({'observed_boot':'old','completed_boot':'old'})
        self.s.connect = lambda: None
        self.s.boot = lambda: 'new'
        self.s.shell = lambda *a: '0'
        self.assertEqual(self.s.tick()['result'], 'waiting_for_boot')
        self.s.shell = lambda *a: '1'
        def fail(): raise mod.RecoveryError('not yet')
        self.s.recover = fail
        for _ in range(3):
            with self.assertRaises(mod.RecoveryError): self.s.tick()
        self.assertEqual(self.s.tick()['result'], 'attempts_exhausted')
        self.assertEqual(self.s.read_state()['completed_boot'], 'old')

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
        self.s.boot = lambda: 'boot'
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
        self.s.boot = lambda: 'boot'
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


if __name__ == '__main__': unittest.main()
