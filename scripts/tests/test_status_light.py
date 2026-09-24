from pathlib import Path
import json
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_status_light import StatusLight, COLORS, restore_patch

class LightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.original={'1':False,'2':'white','3':50,'4':0,'5':'002f03e803e8'}
        self.current=dict(self.original);self.writes=[]
        def backend(patch=None):
            if patch:self.writes.append(dict(patch));self.current.update(patch)
            return dict(self.current)
        self.light=StatusLight({'enabled':True},self.root,backend)
    def tearDown(self):self.light.close();self.temp.cleanup()
    def wait_state(self,state):
        until=time.monotonic()+2
        while time.monotonic()<until:
            try:
                if json.loads(self.light.receipt.read_text())['state']==state:return
            except (OSError,ValueError):pass
            time.sleep(.01)
        self.fail('missing state '+state)
    def test_blue_green_yellow_restore_all_fields(self):
        for color in COLORS:
            self.light.show(color);self.wait_state(color)
            self.assertTrue(self.current['1']);self.assertEqual(self.current['5'],COLORS[color])
        self.assertTrue(self.light.release());self.assertFalse(self.current['1'])
        self.assertFalse(self.light.journal.exists())
    def test_no_followup_restores_after_deadline(self):
        self.light.show('green',ttl=.04);self.wait_state('green');self.wait_state('restored')
        self.assertFalse(self.current['1'])
    def test_external_power_and_color_changes_preserved(self):
        self.light.show('blue');self.wait_state('blue')
        self.current['1']=False;self.current['5']='001003e803e8'
        self.assertTrue(self.light.release())
        self.assertFalse(self.current['1']);self.assertEqual(self.current['5'],'001003e803e8')
        self.assertEqual(self.current['2'],'colour')
    def test_explicit_command_after_release_is_not_undone(self):
        self.light.show('green');self.wait_state('green');self.light.release()
        self.current['1']=True;self.light.release();self.assertTrue(self.current['1'])
    def test_close_restores(self):
        self.light.show('yellow');self.wait_state('yellow');self.light.close()
        self.light.thread=None;self.assertFalse(self.current['1'])
    def test_crash_journal_recovered(self):
        self.light.close();self.light.thread=None
        owned=dict(self.original,**{'1':True,'2':'colour','5':COLORS['blue']});self.current.update(owned)
        self.light._save(self.light.journal,{'original':self.original,'owned':owned})
        old=self.light;self.light=StatusLight({'enabled':True},self.root,old.backend)
        until=time.monotonic()+2
        while self.light.journal.exists() and time.monotonic()<until:time.sleep(.01)
        self.assertFalse(self.current['1'])
    def test_network_failure_does_not_raise_on_show(self):
        def fail(patch=None):raise OSError('private data')
        self.light.backend=fail;self.light.show('blue');self.wait_state('error')
        self.assertNotIn('private',self.light.receipt.read_text())

class DeviceProtocolTests(unittest.TestCase):
    def test_color_auto_on_is_restored_and_new_color_precedes_power_on(self):
        from unittest.mock import patch, Mock
        import types
        from jarvis_status_light import device_request
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'map.json';path.write_text(json.dumps({'devices':{'lamp':{'dev_id':'test','gateway':'g'}},'gateways':{'g':{'ip':'test','local_key':'test'}}}))
            state={'1':False,'2':'white','3':50,'4':0,'5':'000003e803e8'};calls=[]
            device=Mock();device.status.side_effect=lambda:{'dps':dict(state)}
            def set_value(k,v):
                calls.append((k,v));state[k]=v
                if k=='5':state['1']=True
                return {}
            device.set_value.side_effect=set_value
            with patch.dict(sys.modules,{'tinytuya':types.SimpleNamespace(Device=Mock(return_value=device))}),patch('jarvis_status_light.time.sleep'):
                result=device_request({'config':{'map':str(path),'device':'lamp'},'patch':{'5':COLORS['blue']}})
                self.assertFalse(result['dps']['1']);self.assertEqual(calls[-1],('1',False))
                calls.clear()
                result=device_request({'config':{'map':str(path),'device':'lamp'},'patch':{'5':COLORS['green'],'2':'colour','1':True}})
                self.assertEqual(calls[0],('5',COLORS['green']));self.assertEqual(calls[-1],('1',True))
