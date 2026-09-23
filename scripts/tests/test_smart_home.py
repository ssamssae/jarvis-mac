from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_smart_home import SmartHome


class SmartHomeTests(unittest.TestCase):
    def setUp(self):
        self.module=types.SimpleNamespace(
            plan_for_transcript=Mock(side_effect=lambda s:{'matched':True,'intent':s,'steps':[], 'response_text':'제어 신호를 보냈어요.'}),
            tuya_step=lambda c,*a:{'type':'tuya','command':c,'args':list(a)},
            execute_plan=Mock(return_value={'status':'ok','executed':[]}))
        self.config={'module':'unused','python':'python3','control_script':'control.py','scheduler_script':'schedule.sh',
            'phrases':{'나잘거야':'나 잘 거야','게임할거야':'나 게임 할 거야','외출':'외출할게','물줘':'물줘'},
            'devices':{'선풍기':{'routine':'선풍기'},'방불':{'on':['room','on'],'off':['room','off']}},
            'scheduled_device':'에어컨'}
        self.home=SmartHome(self.config,self.module)

    def test_existing_routine_canonical_phrase_passed_unchanged(self):
        for phrase,canonical in [('나 잘 거야','나 잘 거야'),('게임할 거야','나 게임 할 거야'),('외출','외출할게')]:
            self.home.plan(phrase)
            self.module.plan_for_transcript.assert_called_with(canonical)

    def test_device_request_politeness_and_direct_device_mapping(self):
        self.home.plan('선풍기 꺼줄래?')
        self.module.plan_for_transcript.assert_called_with('선풍기 꺼줘')
        plan=self.home.plan('방 불을 켜 주세요')
        self.assertEqual(plan['steps'],[{'type':'tuya','command':'room','args':['on']}])

    def test_questions_negations_quotes_and_unknown_targets_never_dispatch(self):
        for s in ['게임이 뭐야','게임할거야 라고 했어','선풍기 꺼주지 마','선풍기 꺼줄래라고 말해','모르는기기 켜줘','물 마시고 싶다','외출 루틴 설명해줘','선풍기 켜고 물 줘']:
            self.assertIsNone(self.home.plan(s),s)
        self.module.execute_plan.assert_not_called()

    def test_disabled_without_local_config(self):
        self.assertIsNone(SmartHome(None).plan('외출'))

    def test_execute_preserves_steps_and_disables_legacy_tts(self):
        plan={'intent':'sleep','steps':[{'type':'tuya','command':'room','args':['off']}],'response_text':'제어 신호를 보냈어요.'}
        result=self.home.execute(plan,explicit_voice=True)
        self.assertFalse(result['device_state_verified'])
        args=self.module.execute_plan.call_args
        self.assertIs(args.args[0],plan)
        self.assertFalse(args.kwargs['tts_enabled'])
        self.assertTrue(args.kwargs['execute'])
        self.assertFalse(args.kwargs['water_confirmed'])

    def test_water_never_inherits_ambient_approval(self):
        import os
        from unittest.mock import patch
        plan={'intent':'water','requires_water_confirmation':True,'steps':[{'type':'tuya','water':True}]}
        with patch.dict(os.environ,{'TUYA_WATER_CONFIRMED':'1'}):
            self.assertEqual(self.home.execute(plan)['status'],'blocked')
        self.module.execute_plan.assert_not_called()
        self.home.execute(plan,explicit_voice=True,dry_run=True)
        self.assertFalse(self.module.execute_plan.call_args.kwargs['execute'])
        self.assertTrue(self.module.execute_plan.call_args.kwargs['water_confirmed'])

    def test_partial_failure_never_speaks_success_or_retries(self):
        self.module.execute_plan.return_value={'status':'error','executed':[{'step':{'type':'tuya'},'status':'error','returncode':124}]}
        result=self.home.execute({'intent':'sleep','steps':[],'response_text':'완료'})
        self.assertIn('일부',result['answer']);self.assertNotEqual(result['answer'],'완료')
        self.assertEqual(self.module.execute_plan.call_count,1)

    def test_exception_from_executor_becomes_spoken_failure(self):
        self.module.execute_plan.side_effect=OSError('sensitive provider text')
        result=self.home.execute({'intent':'sleep','steps':[]})
        self.assertEqual(result['status'],'error');self.assertNotIn('sensitive',str(result))

if __name__=='__main__':unittest.main()

class ListenerRoutingTests(unittest.TestCase):
    def test_routine_bypasses_cursor_and_playback_error_never_repeats_device(self):
        import io,json,tempfile,time
        from unittest.mock import patch,MagicMock
        import jarvis_mac_listener as app
        for failed_audio in [False,True]:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);audio=root/'audio';audio.mkdir();clip=audio/'one.wav';clip.write_bytes(b'x'*44)
                (root/'config.json').write_text(json.dumps({'cast_name':'fixture','model':'fixture','whisper_cli':'fixture'}))
                now=time.time();event={'wav':str(clip),'speech_started_wall':now-2,'speech_ended_wall':now-1,'capture_ended_wall':now}
                stt=MagicMock();stt.read.return_value={'text':'자비스 나 잘 거야'}
                home=MagicMock();home.plan.return_value={'intent':'sleep','steps':[]}
                home.execute.return_value={'intent':'sleep','status':'ok','answer':'안녕히 주무세요.'}
                speech=MagicMock();speech.first_playing=None;speech.ack_first_playing=None;speech.events=[]
                if failed_audio:speech.finish.side_effect=RuntimeError('cast_playback_error')
                with patch.object(sys,'argv',['listener','--state-dir',str(root)]),patch.object(sys,'stdin',io.StringIO(json.dumps(event)+'\n')),patch.object(sys,'stdout',io.StringIO()),patch.object(app.signal,'signal'),patch.object(app.time,'sleep'),patch.object(app,'SmartHome',return_value=home),patch.object(app,'JSONWorker',return_value=stt),patch.object(app,'CastOutput'),patch.object(app,'SpeechQueue',return_value=speech),patch.object(app,'CursorQA') as qa,patch.object(app,'run_turn') as turn:
                    app.main()
                qa.assert_not_called();turn.assert_not_called();home.execute.assert_called_once()
                self.assertTrue(home.execute.call_args.kwargs['explicit_voice'])
                receipt=json.loads((root/'last-turn.json').read_text())
                self.assertEqual(receipt['smart_home']['status'],'ok')
                self.assertEqual(receipt['result'],'error' if failed_audio else 'pass')
                if failed_audio:self.assertEqual(receipt['error_code'],'cast_playback_error')
