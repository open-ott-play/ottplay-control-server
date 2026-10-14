import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_kiosk', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

class KioskTest(unittest.TestCase):
    def test_commands(self):
        for words, params in [
            (['kiosk'], {'mode':'status'}),
            (['KIOSK','STATUS'], {'mode':'status'}),
            (['kiosk','on'], {'mode':'on'}),
            (['kiosk','on','--strict'], {'mode':'on','strict':True}),
            (['kiosk','on','--strict','Новости','HD'], {'mode':'on','strict':True,'query':'Новости HD'}),
            (['kiosk','set','12','--strict'], {'mode':'set','strict':True,'query':'12'}),
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
        self.assertEqual(ott.kiosk_metadata({**data,'provider':'plex'},'on')['provider'], 'plex')
        result=ott.kiosk_metadata(data,'on')
        self.assertEqual(result['media'],{'title':'Episode','index':0,'total':2})
        for patch in [{'provider':'m3u'},{'channel':{'id':'a','name':'Channel'}},{'media':{'title':'Episode','index':2,'total':2}}]:
            with self.subTest(patch=patch),self.assertRaises(ott.Error):
                ott.kiosk_metadata({**data,**patch},'on')

    def test_invalid_commands(self):
        for words in [['kiosk','set'],['kiosk','other'],['kiosk','off','1'],['kiosk','status','1'],['kiosk','on',''],['kiosk','set','\ud800'],['kiosk','set','a\n'],['kiosk','set','я'*513]]:
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(words)
        for words in [['kiosk','off','--strict'],['kiosk','status','--strict'],['kiosk','set','--strict'],['kiosk','on','--strict','--strict']]:
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(words)

    def test_strict_requires_explicit_confirmation(self):
        data={'enabled':True,'state':'locked','channel':{'id':'a','name':'News'},'provider':'m3u','retry_seconds':10,'retries':0,'health':'starting'}
        for strict in [None,False,1,'true']:
            value = data if strict is None else {**data,'strict':strict}
            with self.subTest(strict=strict), self.assertRaises(ott.Error):
                ott.kiosk_metadata(value,'on',True)
        self.assertTrue(ott.kiosk_metadata({**data,'strict':True},'on',True)['strict'])
        self.assertFalse(ott.kiosk_metadata({**data,'strict':False},'on')['strict'])

    def test_strict_command_requires_confirmed_receipt(self):
        data={'enabled':True,'state':'locked','channel':{'id':'a','name':'News'},'provider':'m3u','retry_seconds':10,'retries':0,'health':'starting'}
        for machine in [False, True]:
            for strict in [None, False, True]:
                with self.subTest(machine=machine, strict=strict):
                    client = mock.Mock(timeout=45)
                    client.device.return_value = 'dev_tv'
                    client.call.return_value = data if strict is None else {**data, 'strict':strict}
                    output, errors = io.StringIO(), io.StringIO()
                    with mock.patch.object(ott, 'Client', return_value=client), \
                            mock.patch.object(ott, 'read_json', return_value={}), \
                            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        code = ott.main((['--json'] if machine else []) + ['tv', 'kiosk', 'on', '--strict'])
                    client.call.assert_called_once_with('dev_tv', 'kiosk', {'mode':'on', 'strict':True})
                    if strict is True:
                        self.assertEqual((code, errors.getvalue()), (0, ''))
                        if machine:
                            self.assertTrue(json.loads(output.getvalue())['strict'])
                        else:
                            self.assertIn('Strict kiosk:', output.getvalue())
                    else:
                        self.assertEqual((code, output.getvalue()), (1, ''))
                        self.assertIn('did not confirm the kiosk policy', errors.getvalue())

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

    def test_optional_recovery_metadata_is_validated_and_preserved(self):
        data = {'enabled':True, 'state':'locked', 'channel':None, 'provider':'plex',
                'retry_seconds':10, 'retries':0, 'health':'waiting',
                'media':{'title':'Episode', 'index':0, 'total':2},
                'screen_suspended':True, 'startup_grace_seconds':60}
        self.assertEqual(ott.kiosk_metadata(data, 'status'), data)
        for patch in [{'screen_suspended':1}, {'screen_suspended':'true'},
                      {'startup_grace_seconds':True}, {'startup_grace_seconds':10},
                      {'startup_grace_seconds':'60'}]:
            with self.subTest(patch=patch), self.assertRaises(ott.Error):
                ott.kiosk_metadata({**data, **patch}, 'status')
        waiting = {**data, 'state':'waiting', 'media':None}
        with self.assertRaises(ott.Error):
            ott.kiosk_metadata(waiting, 'status')
        waiting.pop('startup_grace_seconds')
        with self.assertRaises(ott.Error):
            ott.kiosk_metadata(waiting, 'status')
        waiting['screen_suspended'] = False
        self.assertFalse(ott.kiosk_metadata(waiting, 'status')['screen_suspended'])

    def test_status_identifies_media_provider_and_screen_suspension(self):
        for provider, name in [('plex', 'Plex'), ('vportal', 'VPortal')]:
            for machine in [False, True]:
                with self.subTest(provider=provider, machine=machine):
                    data = {'enabled':True, 'state':'locked', 'retry_seconds':10, 'retries':0,
                            'health':'waiting', 'channel':None, 'provider':provider,
                            'media':{'title':'Episode', 'index':0, 'total':2},
                            'strict':True, 'screen_suspended':True, 'startup_grace_seconds':60}
                    client = mock.Mock(timeout=45)
                    client.device.return_value = 'dev_tv'
                    client.call.return_value = data
                    output, errors = io.StringIO(), io.StringIO()
                    with mock.patch.object(ott, 'Client', return_value=client), \
                            mock.patch.object(ott, 'read_json', return_value={}), \
                            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        code = ott.main((['--json'] if machine else []) + ['tv', 'kiosk', 'status'])
                    client.call.assert_called_once_with('dev_tv', 'kiosk', {'mode':'status'})
                    if machine:
                        expected = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
                    else:
                        expected = (
                            'Strict kiosk: local controls locked; supported players allow timeline seeking within the current media episode.\n'
                            f'Kiosk {name}: Episode | 1/2 | waiting | retries: 0\n'
                            'Kiosk playback is suspended while the screen is off or the app is in the background.\n'
                        )
                    self.assertEqual((code, output.getvalue(), errors.getvalue()), (0, expected, ''))

    def test_strict_waiting_does_not_claim_controls_are_already_locked(self):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.return_value = {'enabled':True, 'state':'waiting', 'channel':None, 'provider':'m3u',
                                    'retry_seconds':10, 'retries':0, 'health':'waiting', 'strict':True}
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(['tv', 'kiosk', 'on', '--strict'])
        client.call.assert_called_once_with('dev_tv', 'kiosk', {'mode':'on', 'strict':True})
        self.assertEqual((code, output.getvalue(), errors.getvalue()), (0, (
            'Strict kiosk is armed; local controls lock after the first channel selection.\n'
            'Kiosk mode enabled; waiting for the first channel selection in the player.\n'
        ), ''))

if __name__=='__main__':
    unittest.main()
