from pathlib import Path
import json
import sys
import subprocess
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_work_mode import matches,execute
from jarvis_display_brightness import brightness

class WorkTests(unittest.TestCase):
    def test_only_exact_commands(self):
        for text in ['일하자','일 시작하자!','작업 시작하자']:self.assertTrue(matches(text))
        for text in ['일하자라고 말해','일하지마','내일하자','일하자 그리고 물줘']:self.assertFalse(matches(text))
    def test_fixed_argv_and_verified_brightness(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps({'verified':True,'level':6}),''))
        result=execute({'steps':[{'name':'맥','kind':'brightness','argv':['python3','helper.py','--level','6']}]},run)
        self.assertEqual(result['status'],'ok');self.assertIn('6칸',result['answer'])
        self.assertEqual(run.call_args.args[0],['python3','helper.py','--level','6'])
        self.assertNotIn('shell',run.call_args.kwargs)
    def test_packets_not_claimed_as_boot_and_missing_target_is_partial(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,'[wol-fleet] woke pc via relay',''))
        result=execute({'steps':[{'name':'PC','kind':'wake','argv':['wake'],'success_marker':'woke pc'},{'name':'미등록PC','kind':'wake'}]},run)
        self.assertEqual(result['status'],'partial');self.assertIn('부팅 완료는 아직',result['answer']);self.assertEqual(run.call_count,1)
    def test_failed_commands_and_invalid_receipts_never_claim_success(self):
        item={'name':'맥','kind':'brightness','argv':['helper']}
        for stdout in ['{}','not json',json.dumps({'verified':True,'level':5})]:
            result=execute({'steps':[item]},Mock(return_value=subprocess.CompletedProcess([],0,stdout,'')))
            self.assertEqual(result['status'],'partial')
        result=execute({'steps':[item]},Mock(side_effect=subprocess.TimeoutExpired('helper',15)))
        self.assertEqual(result['steps'][0]['status'],'failed')
        item={'name':'PC','kind':'wake','argv':['helper'],'success_marker':'woke pc'}
        result=execute({'steps':[item]},Mock(return_value=subprocess.CompletedProcess([],0,'skipped','')))
        self.assertEqual(result['steps'][0]['status'],'unverified')
    def test_brightness_out_of_range_rejected_before_system_access(self):
        for level in [-1,17,6.5,True]:
            with self.assertRaises(ValueError):brightness(level)

class WorkRoutingTests(unittest.TestCase):
    def test_voice_work_command_bypasses_model_and_releases_light_first(self):
        import io,tempfile,time
        from unittest.mock import patch,MagicMock
        import jarvis_mac_listener as app
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'audio').mkdir();clip=root/'audio'/'one.wav';clip.write_bytes(b'x'*44)
            (root/'config.json').write_text(json.dumps({'cast_name':'fixture','model':'fixture','work_mode':{'steps':[]}}))
            now=time.time();event={'wav':str(clip),'speech_started_wall':now-2,'speech_ended_wall':now-1,'capture_ended_wall':now}
            stt=MagicMock();stt.read.return_value={'text':'자비스 일하자'}
            home=MagicMock();home.plan.return_value=None
            speech=MagicMock();speech.first_playing=None;speech.ack_first_playing=None;speech.events=[]
            indicator=MagicMock();order=[];indicator.release.side_effect=lambda:order.append('release') or True
            def execute(config):order.append('work');return {'status':'ok','answer':'준비 신호를 보냈어요.'}
            with patch.object(sys,'argv',['listener','--state-dir',str(root)]),patch.object(sys,'stdin',io.StringIO(json.dumps(event)+'\n')),patch.object(sys,'stdout',io.StringIO()),patch.object(app.signal,'signal'),patch.object(app,'SmartHome',return_value=home),patch.object(app,'JSONWorker',return_value=stt),patch.object(app,'CastOutput'),patch.object(app,'SpeechQueue',return_value=speech),patch.object(app,'StatusLight',return_value=indicator),patch.object(app,'CursorQA') as qa,patch.object(app.jarvis_work_mode,'execute',side_effect=execute):
                app.main()
            qa.assert_not_called();home.execute.assert_not_called();self.assertEqual(order[:2],['release','work'])
            self.assertEqual(json.loads((root/'last-turn.json').read_text())['work_mode']['status'],'ok')
