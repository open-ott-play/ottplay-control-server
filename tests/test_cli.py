import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('ott', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

class CliTest(unittest.TestCase):
    def test_volume(self):
        self.assertEqual(ott.parse_command(['V']), ('status', {}))
        for text, field, value in [('35','volume',35),('+5','volume_step',5),('-12','volume_step',-12),('0','volume',0)]:
            self.assertEqual(ott.parse_command(['v',text]), ('command',{'command':'set_volume',field:value}))
        for value in ['nan','inf','1e999','101','1 2']:
            with self.assertRaises(ott.Error): ott.parse_command(['v',value])
    def test_queries(self):
        self.assertEqual(ott.parse_command(['S','НовоСТИ']),('channels',{'search':'НовоСТИ'}))
        self.assertEqual(ott.parse_command(['p','НАУКА']),('programs',{'search':'НАУКА'}))
        self.assertEqual(ott.parse_command(['12']),('play',{'query':'12'}))
        self.assertEqual(ott.parse_command(['Первый','HD']),('play',{'query':'Первый HD'}))
        self.assertEqual(ott.parse_command(['play','s']),('play',{'query':'s'}))
        self.assertEqual(ott.parse_command(['provider','M3U']),('provider',{'query':'M3U'}))
    def test_private_config_and_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'server.json'
            ott.write_private(path, {'admin_token':'a'*32,'devices':[{'id':'dev_abc','token':'b'*32}]})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            client=ott.Client({'server':'http://127.0.0.1:8081','server_config':str(path),'players':{'TV':'dev_abc'}})
            self.assertEqual(client.device('tv'),'dev_abc')
            with self.assertRaises(ott.Error): client.device('missing')
    def test_terminal_control(self):
        self.assertNotIn('\x1b',ott.clean('\x1b[31mnews\n'))

if __name__=='__main__': unittest.main()
