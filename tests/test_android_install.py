import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('android_install', Path(__file__).resolve().parents[1]/'native/android-agent/install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

class AndroidInstall(unittest.TestCase):
    def run_install(self, previous, model='Q1001L4B2', fail_version=False):
        commands=[]; pushes=[]
        def shell(command):
            commands.append(command)
            if command.startswith('getprop'):return model+'\n12345678\n19\n'
            if command=='id':return 'uid=0(root)'
            if command=='cat '+install.BOOT_PATH:return previous
            if fail_version and '--version' in command:return ''
            return 'OTT_NATIVE_OK\n'
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config';config.write_text(json.dumps({'model':'Q1001L4B2','serial':'12345678','device':'native','parent_device':'web'}))
            try:
                install.install(shell, lambda *args:pushes.append(args), config, '/test/agent', '/test/boot')
            except RuntimeError:
                return commands,pushes,False
        return commands,pushes,True
    def test_unknown_boot_and_wrong_device_are_read_only(self):
        for previous,model in [('foreign','Q1001L4B2'),(install.TOUCH_BOOT,'other')]:
            commands,pushes,ok=self.run_install(previous,model)
            self.assertFalse(ok);self.assertEqual(pushes,[])
            self.assertFalse(any('mount' in x or 'stop flash_recovery' in x for x in commands))
    def test_execution_check_precedes_stopping_services(self):
        commands,_,ok=self.run_install(install.TOUCH_BOOT,fail_version=True)
        self.assertFalse(ok);self.assertFalse(any('stop flash_recovery' in x for x in commands))
    def test_keeps_touch_service_and_restores_readonly_system(self):
        commands,pushes,ok=self.run_install(install.TOUCH_BOOT)
        self.assertTrue(ok);self.assertEqual(len(pushes),3)
        self.assertIn('/system/bin/ott-touch-service',install.BOOT)
        ro=next(i for i,x in enumerate(commands) if 'remount,ro' in x)
        start=next(i for i,x in enumerate(commands) if 'start flash_recovery' in x)
        self.assertLess(ro,start)
