import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_presets_cli import config, ott, PORTAL
from test_preset_session import ExactPlayer, session


class PortalPlayer(ExactPlayer):
    def __init__(self):
        super().__init__()
        self.portals = {1: {'name':'Keep VPortal name','vportal':''}, 2:{'name':'Other','vportal':'untouched'}}
        self.revision = 0
        self.portal_writes = []
        self.standby = True
        self.stale = False
        self.lose_reply = False
        self.available = True

    def call(self, device, action, params, idempotency_key=None):
        if action == 'capabilities':
            value = {'version':1,'player':{'version':'1.1.60','platform':'browser','runtime':'page-123'},
                     'lifecycle':[],'input':[],'playback':[]}
            if self.available: value['vportal_settings'] = {'version':1}
            self.calls.append((device,action,copy.deepcopy(params)))
            return value
        if action == 'settings_compare' and params['provider'] == 'vportal':
            self.calls.append((device,action,copy.deepcopy(params)))
            number = params['number']
            return {'provider':'vportal','number':number,'runtime':'page-123','context':'source-1',
                    'revision':'revision-' + str(self.revision),
                    'matched':all(self.portals[number][key] == value for key,value in params['settings'].items())}
        if action == 'provider_settings' and params['provider'] == 'vportal':
            self.calls.append((device,action,copy.deepcopy(params)))
            if self.stale:
                raise ott.PlayerRejected('do not echo ' + PORTAL, {})
            assert params['context'] == 'source-1'
            assert params['revision'] == 'revision-' + str(self.revision)
            row = params['settings']
            number = row['profile']
            self.portal_writes.append((number,self.provider,self.standby))
            self.portals[number].update({key:value for key,value in row.items() if key != 'profile'})
            self.revision += 1
            if idempotency_key:
                self.operations[idempotency_key] = {'state':'responded','result_status':'ok'}
            if self.lose_reply:
                self.lose_reply = False
                raise ott.Error('reply lost')
            return {'provider':'vportal','profile':number,'runtime':'page-123','context':'source-1',
                    'revision':'revision-' + str(self.revision),'fields':list(row),'saved':True,
                    'reloaded':False,'reload_required':False}
        return super().call(device, action, params, idempotency_key)


def preset_source():
    source = config()
    source['presets']['local']['m3u'][0]['vportal'] = PORTAL
    return source


class VPortalSettingsTests(unittest.TestCase):
    def test_legacy_migration_preserves_target_name_and_other_slots(self):
        source = preset_source(); before = copy.deepcopy(source)
        name, preset = ott.validate_preset(source,'Local')
        player = PortalPlayer()
        receipt = ott.load_preset(player,'dev_tv',name,preset)
        self.assertEqual(receipt['status'],'loaded')
        self.assertEqual(player.portals[1],{'name':'Keep VPortal name','vportal':PORTAL})
        self.assertEqual(player.portals[2],{'name':'Other','vportal':'untouched'})
        self.assertEqual(player.portal_writes,[(1,'m3u',True)])
        self.assertTrue(player.standby)
        self.assertEqual(source,before)
        self.assertNotIn(PORTAL,json.dumps(receipt))
        self.assertFalse(any(action == 'provider' and params['query'] == 'vportal' for _,action,params in player.calls))

    def test_explicit_entry_deduplicates_equal_link_and_may_rename(self):
        source = preset_source()
        source['presets']['local']['vportal'] = [{'number':1,'vportal':PORTAL,'name':'Dedicated'}]
        name,preset = ott.validate_preset(source,'local')
        self.assertEqual(len(preset['vportal']),1)
        player = PortalPlayer()
        self.assertEqual(ott.load_preset(player,'dev_tv',name,preset)['status'],'loaded')
        self.assertEqual(player.portals[1]['name'],'Dedicated')
        source['presets']['local']['vportal'][0]['vportal'] = 'different-secret-link'
        with self.assertRaisesRegex(ott.Error,'conflict') as error:
            ott.validate_preset(source,'local')
        self.assertNotIn('different-secret-link',str(error.exception))
        self.assertNotIn(PORTAL,str(error.exception))

    def test_old_player_fails_before_any_mutation(self):
        name,preset = ott.validate_preset(preset_source(),'local')
        player = PortalPlayer(); player.available = False
        receipt = ott.load_preset(player,'dev_tv',name,preset)
        self.assertEqual(receipt['status'],'failed')
        self.assertEqual([action for _,action,_ in player.calls],['capabilities'])
        self.assertEqual(player.portal_writes,[])

    def test_revision_conflict_never_retries_or_switches_provider(self):
        name,preset = ott.validate_preset(preset_source(),'local')
        player = PortalPlayer(); player.stale = True
        receipt = ott.load_preset(player,'dev_tv',name,preset)
        self.assertEqual(receipt['status'],'failed')
        self.assertEqual(sum(action == 'provider_settings' for _,action,_ in player.calls),1)
        self.assertFalse(any(action == 'provider' for _,action,_ in player.calls))
        self.assertNotIn(PORTAL,json.dumps(receipt))

    def test_lost_reply_resume_compares_dedicated_slot_without_rewriting(self):
        name,preset = ott.validate_preset(preset_source(),'local')
        player = PortalPlayer(); player.lose_reply = True
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'checkpoint.json'
            for resume, expected in [(False,'failed'),(True,'loaded')]:
                current = session.Session(ott,player,'dev_tv',name,preset,path,resume)
                try: self.assertEqual(ott.load_preset(player,'dev_tv',name,preset,current)['status'],expected)
                finally: current.close()
            self.assertEqual(len(player.portal_writes),1)
            self.assertEqual(session.verify(ott,player,'dev_tv',preset)['status'],'verified')
            self.assertNotIn(PORTAL,path.read_text())

    def test_old_checkpoint_omitting_legacy_portal_is_rejected(self):
        name,old = ott.validate_preset(config(),'local')
        _,new = ott.validate_preset(preset_source(),'local')
        player = PortalPlayer()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'checkpoint.json'
            current = session.Session(ott,player,'dev_tv',name,old,path,False); current.close()
            with self.assertRaisesRegex(ott.Error,'revision'):
                session.Session(ott,player,'dev_tv',name,new,path,True)
        self.assertEqual(player.calls,[])


if __name__ == '__main__': unittest.main()
