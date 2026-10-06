import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ott_kiosk', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

class KioskTest(unittest.TestCase):
    def test_commands(self):
        for words, params in [
            (['kiosk'], {'mode':'status'}),
            (['KIOSK','STATUS'], {'mode':'status'}),
            (['kiosk','on'], {'mode':'on'}),
            (['kiosk','on','12'], {'mode':'on','query':'12'}),
            (['kiosk','on','нОвОсТи'], {'mode':'on','query':'нОвОсТи'}),
            (['kiosk','set','ВоСт'], {'mode':'set','query':'ВоСт'}),
            (['kiosk','set','Новости','HD'], {'mode':'set','query':'Новости HD'}),
            (['kiosk','off'], {'mode':'off'}),
        ]:
            self.assertEqual(ott.parse_command(words), ('kiosk',params))
        self.assertEqual(ott.parse_command(['play','kiosk']), ('play',{'query':'kiosk'}))

    def test_vportal_receipt_is_metadata_only(self):
        data={'enabled':True,'state':'locked','channel':None,'provider':'vportal','retry_seconds':10,'retries':0,'health':'starting',
              'media':{'title':'Episode','index':0,'total':2,'request':{'private':'secret'}}}
        result=ott.kiosk_metadata(data,'on')
        self.assertEqual(result['media'],{'title':'Episode','index':0,'total':2})
        for patch in [{'provider':'m3u'},{'channel':{'id':'a','name':'Channel'}},{'media':{'title':'Episode','index':2,'total':2}}]:
            with self.subTest(patch=patch),self.assertRaises(ott.Error):
                ott.kiosk_metadata({**data,**patch},'on')

    def test_invalid_commands(self):
        for words in [['kiosk','set'],['kiosk','other'],['kiosk','off','1'],['kiosk','status','1'],['kiosk','on',''],['kiosk','set','\ud800'],['kiosk','set','a\n'],['kiosk','set','я'*513]]:
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(words)

    def test_receipts_and_metadata_only_output(self):
        data={'enabled':True,'state':'locked','channel':{'id':'a','name':'Новости','url':'secret'},'provider':'m3u','retry_seconds':10,'retries':0,'health':'starting','source':'private'}
        result=ott.kiosk_metadata(data,'set')
        self.assertNotIn('source',result)
        self.assertNotIn('url',result['channel'])
        for patch in [{'enabled':False},{'state':'off'},{'retry_seconds':1},{'retries':True},{'channel':None},{'health':'unknown'}]:
            with self.subTest(patch=patch), self.assertRaises(ott.Error):
                ott.kiosk_metadata({**data,**patch},'set')
        with self.assertRaises(ott.Error):
            ott.kiosk_metadata(data,'off')
        off={'enabled':False,'state':'off','channel':None,'provider':None,'retry_seconds':10,'retries':0,'health':'idle'}
        self.assertEqual(ott.kiosk_metadata(off,'off'),off)
        with self.assertRaises(ott.Error):
            ott.kiosk_metadata(off,'on')

if __name__=='__main__':
    unittest.main()
