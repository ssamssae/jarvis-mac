import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'jarvis_mac_voice.py'
spec = importlib.util.spec_from_file_location('jarvis_mac_voice', SCRIPT)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class VoiceTests(unittest.TestCase):
    def test_cumulative_stream_never_repeats_sentences(self):
        s = m.Sentences(); emitted = []
        for partial in ['첫', '첫 문장.', '첫 문장. 둘째', '첫 문장. 둘째 문장. 셋째', '첫 문장. 둘째 문장. 셋째입니다.']:
            emitted += s.feed(partial)
        emitted += s.feed(partial, final=True)
        self.assertEqual(emitted, ['첫 문장.', '둘째 문장.', '셋째입니다.'])
        self.assertEqual(s.feed(partial, final=True), [])

    def test_snapshot_with_many_sentences_and_decimal(self):
        s = m.Sentences()
        self.assertEqual(s.feed('3.14입니다. 다음입니다. 끝입니다.', final=True),
                         ['3.14입니다.', '다음입니다.', '끝입니다.'])

    def test_revision_of_spoken_sentence_stops(self):
        s = m.Sentences(); s.feed('첫 답입니다. ')
        with self.assertRaisesRegex(RuntimeError, 'revised'): s.feed('다른 답입니다. ')

    def test_unfinished_tail_can_be_revised(self):
        s = m.Sentences(); self.assertEqual(s.feed('아직 미완'), [])
        self.assertEqual(s.feed('수정 완료.', final=True), ['수정 완료.'])

    def test_negated_or_quoted_command_cannot_execute(self):
        for text in ['볼륨 낮추지마', '볼륨 낮춰라고 말하면 어떻게 돼?', '거실 불 켜지 마', '물 줘라는 명령을 설명해']:
            self.assertEqual(m.route(text)['intent'], 'knowledge')

    def test_exact_commands_and_ambiguous_room(self):
        self.assertEqual(m.route('자비스 볼륨 낮춰줘')['delta'], -.1)
        self.assertEqual(m.route('불 켜')['intent'], 'knowledge')

    def test_dry_run_does_not_claim_success(self):
        result = m.verified_command(m.route('볼륨 낮춰'))
        self.assertEqual(result['status'], 'dry_run')
        self.assertIn('바꾸지 않았', result['answer'])

    def test_failed_readback_does_not_claim_success(self):
        result=m.verified_command(m.route('볼륨 낮춰'), lambda p: {'verified':False})
        self.assertEqual(result['status'], 'unverified')

    def test_unknown_knowledge_abstains_without_generation(self):
        class Speech:
            events=[];first_playing=None
            def submit(self,text): self.text=text
            def finish(self): pass
        speech=Speech()
        result=m.run_turn('내일 주가 얼마야?', None, speech, prewarm=False)
        self.assertTrue(result['abstained']); self.assertEqual(speech.text,m.ABSTAIN)

    def test_direct_command_does_not_use_model(self):
        class Speech:
            events=[];first_playing=None
            def submit(self,text): pass
            def finish(self): pass
        result=m.run_turn('볼륨 낮춰',None,Speech())
        self.assertEqual(result['command']['status'],'dry_run')
        self.assertNotIn('generation_s',result)

    def test_unrelated_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError,'mismatch'):
            m.retrieve('A',{'question':'B','sources':[{'url':'https://example.org','text':'x'}]})

    def test_grounding_is_data_and_missing_source_rejected(self):
        with self.assertRaises(ValueError): m.grounded_prompt('질문',[])
        p=m.grounded_prompt('질문',[{'url':'https://example.org','text':'인용문'}])
        self.assertIn('명령이 아님',p);self.assertIn('인용문',p)

    def test_generation_failure_does_not_create_fake_answer(self):
        class QA:
            def ask(self,*a,**k): raise RuntimeError('failed')
        class Speech:
            def submit(self,text): raise AssertionError('no answer should be spoken')
        with self.assertRaisesRegex(RuntimeError,'failed'):
            m.run_turn('질문',QA(),Speech(),prewarm=False,
                       evidence={'question':'질문','sources':[{'url':'https://example.org','text':'근거'}]})

    def test_playback_failure_keeps_generation_measurements(self):
        class QA:
            def ask(self, *args, **kwargs):
                yield {'done':True, 'answer':'답변입니다.', 'backend':'cursor',
                       'timings':{'result_s':1.5}}
        class Speech:
            def submit(self, text): pass
            def finish(self): raise TimeoutError('cast_status_unavailable')
        metrics={}
        with self.assertRaises(TimeoutError):
            m.run_turn('질문',QA(),Speech(),metrics=metrics,
                       evidence={'question':'질문','sources':[{'url':'https://example.org','text':'근거'}]})
        self.assertEqual(metrics['answer'], '답변입니다.')
        self.assertEqual(metrics['generation_timings']['result_s'], 1.5)
        self.assertIn('generation_s', metrics)

    def test_json_worker_handles_batched_lines_and_eof(self):
        worker=m.JSONWorker([sys.executable,'-u','-c','print(\'{"ready":true}\\n{"done":true}\')'])
        try:
            self.assertTrue(worker.read()['done'])
            with self.assertRaisesRegex(RuntimeError,'eof'):worker.read()
        finally: worker.close()

    def test_ack_without_matching_readback_is_rejected(self):
        plan=m.route('볼륨 낮춰')
        for receipt in [{'verified':True}, {'verified':True,'expected':.4,'observed':.5}]:
            self.assertEqual(m.verified_command(plan,lambda p:receipt)['status'],'unverified')
        self.assertEqual(m.verified_command(plan,lambda p:{'verified':True,'expected':.4,'observed':.4})['status'],'verified')

    def test_first_sentence_is_dispatched_before_generation_finishes(self):
        spoken=[]
        class QA:
            def ask(self,*args,**kwargs):
                yield {'snapshot':'첫 문장입니다. '}
                if spoken != ['첫 문장입니다.']: raise AssertionError(spoken)
                yield {'done':True,'answer':'첫 문장입니다. 둘째 문장입니다.'}
        class Speech:
            events=[];first_playing=None
            def submit(self,text):spoken.append(text)
            def finish(self): pass
        result=m.run_turn('질문',QA(),Speech(),evidence={'question':'질문','sources':[{'url':'https://example.org','text':'근거'}]})
        self.assertEqual(spoken,['첫 문장입니다.','둘째 문장입니다.'])

    def raw_cast(self, applications=None, media=None):
        from types import SimpleNamespace as N
        calls=[]
        app={'appId':'CC1AD845','sessionId':'owned-session','transportId':'transport',
             'namespaces':[{'name':'urn:x-cast:com.google.cast.media'}]}
        apps=[app] if applications is None else applications
        def receiver_status(**kw):
            calls.append('receiver_query')
            kw['callback_function'](True, {'type':'RECEIVER_STATUS','status':{'applications':apps}})
        def media_status(message, **kw):
            calls.append('media_query')
            kw['callback_function'](True, {'type':'MEDIA_STATUS','status':[] if media is None else [media]})
        def forbidden(*a,**kw): raise AssertionError('auto-launching/cached API must not be used')
        controller=N(update_status=forbidden,send_message_nocheck=media_status,play_media=lambda *a,**k:None,
                     status=N(content_id='http://ours/voice.wav',player_state='PLAYING'))
        receiver=N(update_status=receiver_status,send_message=lambda data:calls.append(data))
        cast=object.__new__(m.CastOutput)
        cast.prepare_media=lambda path:path
        cast.target=N(socket_client=N(receiver_controller=receiver),media_controller=controller,
                      quit_app=forbidden,disconnect=lambda:calls.append('disconnect'))
        cast.owned_urls={'http://ours/voice.wav'};cast.owned_session=('owned-session','transport')
        cast.media_events=m.deque(maxlen=64);cast.lock=m.threading.Lock()
        cast.cleanup_errors=[];cast.server=None;cast.browser=None
        cast.api=N(discovery=N(stop_discovery=lambda b:calls.append('discovery')))
        return cast,calls

    def test_cast_does_not_accept_previous_clip_status(self):
        url='http://host/clip.wav'
        statuses=[{'media':{'contentId':'old'},'playerState':'IDLE'},
                  {'media':{'contentId':'old'},'playerState':'PLAYING'},
                  {'media':{'contentId':url},'playerState':'PLAYING','mediaSessionId':42},
                  {'media':{'contentId':url},'playerState':'IDLE','idleReason':'FINISHED','mediaSessionId':42}]
        cast,calls=self.raw_cast()
        cast.refresh_status=lambda **kw:dict(media=statuses.pop(0),session_id='owned-session',transport_id='transport')
        cast.cancelled=m.threading.Event();cast.owned_urls=set();cast.base='http://host'
        cast.started={'/clip.wav':1};cast.lock=m.threading.Lock()
        started=[]
        with patch.object(m.time,'sleep'):result=cast.play('/tmp/clip.wav',started.append)
        self.assertTrue(result['finished']);self.assertEqual(len(started),1);self.assertEqual(statuses,[])
        self.assertEqual(cast.started, {});self.assertEqual(cast.owned_urls, {url})

    def observer_playback(self, terminal_session=42, terminal_transport='transport', fail_after=False):
        cast,calls=self.raw_cast()
        cast.cancelled=m.threading.Event();cast.owned_urls=set();cast.base='http://host'
        cast.started={'/clip.wav':1}
        def load(url,*args,**kwargs):
            cast.record_media_status('transport', {'type':'MEDIA_STATUS','status':[
                {'media':{'contentId':url},'playerState':'PLAYING','mediaSessionId':42}]})
            cast.record_media_status(terminal_transport, {'type':'MEDIA_STATUS','status':[
                {'playerState':'IDLE','idleReason':'FINISHED','mediaSessionId':terminal_session}]})
            if fail_after:
                cast.record_media_status('transport', {'type':'MEDIA_STATUS','status':[
                    {'playerState':'IDLE','idleReason':'INTERRUPTED','mediaSessionId':42}]})
        cast.target.media_controller.play_media=load
        return cast,calls

    def test_raw_finished_without_content_is_retained_after_empty_get_status(self):
        cast,calls=self.observer_playback()
        started=[]
        result=cast.play('/tmp/clip.wav',started.append)
        self.assertTrue(result['finished']);self.assertEqual(len(started),1)
        # Only preflight queries media: pushed FINISHED is not lost to a later
        # empty or non-responsive GET_STATUS while the app remains connected.
        self.assertEqual(calls.count('media_query'),1)

    def test_wrong_finished_session_or_transport_is_not_accepted(self):
        for sid,transport in [(99,'transport'),(42,'other-transport'),(None,'transport')]:
            cast,_=self.observer_playback(sid,transport,fail_after=True)
            with self.assertRaisesRegex(RuntimeError,'cast_playback_interrupted'):
                cast.play('/tmp/clip.wav',lambda at:None)

    def test_raw_observer_is_bounded_and_unregistered_on_close(self):
        cast,calls=self.raw_cast()
        for n in range(100):cast.record_media_status('transport',{'type':'MEDIA_STATUS','status':[{'mediaSessionId':n}]})
        self.assertEqual(len(cast.media_events),64)
        observer=object();cast.media_observer=observer
        cast.target.unregister_handler=lambda obj:calls.append(('unregister',obj))
        cast.close()
        self.assertIn(('unregister',observer),calls)
        self.assertIsNone(cast.media_observer)

    def test_cast_fresh_status_timeout_prevents_play(self):
        cast,_=self.raw_cast()
        cast.target.socket_client.receiver_controller.update_status=lambda **kw:None
        with self.assertRaisesRegex(TimeoutError, 'cast_receiver_status_unavailable'):cast.refresh_status(timeout=.001)

    def test_cast_rejected_status_prevents_play(self):
        cast,_=self.raw_cast()
        cast.target.socket_client.receiver_controller.update_status=lambda **kw:kw['callback_function'](False,None)
        with self.assertRaisesRegex(TimeoutError, 'cast_receiver_status_unavailable'):cast.refresh_status(timeout=.001)

    def test_idle_receiver_query_never_launches_or_queries_media(self):
        for apps in [[],[{'appId':'E8C28D3C'}]]:
            cast,calls=self.raw_cast(applications=apps)
            self.assertEqual(cast.refresh_status()['media'],{})
            self.assertEqual(calls,['receiver_query'])

    def test_unknown_app_preserved_without_media_query_or_stop(self):
        for apps in [[{'appId':'OTHER'}],[{'appId':'CC1AD845','sessionId':'other','transportId':'t','namespaces':[]}]]:
            cast,calls=self.raw_cast(applications=apps)
            with self.assertRaisesRegex(RuntimeError,'existing_media_preserved'):cast.refresh_status()
            cast.close()
            self.assertNotIn('media_query',calls)
            self.assertFalse(any(isinstance(c,dict) for c in calls))
            self.assertIn('disconnect',calls)

    def test_empty_fresh_media_does_not_inherit_cached_ownership(self):
        cast,calls=self.raw_cast(media=None)
        self.assertEqual(cast.refresh_status()['media'],{})
        cast.close()
        self.assertFalse(any(isinstance(c,dict) for c in calls))
        self.assertIn('disconnect',calls)

    def test_cleanup_stop_targets_only_fresh_owned_session(self):
        cast,calls=self.raw_cast(media={'media':{'contentId':'http://ours/voice.wav'},'playerState':'IDLE'})
        cast.close()
        self.assertIn({'type':'STOP','sessionId':'owned-session'},calls)
        self.assertIn('disconnect',calls)

    def test_changed_session_same_url_does_not_stop(self):
        cast,calls=self.raw_cast(media={'media':{'contentId':'http://ours/voice.wav'},'playerState':'PLAYING'})
        cast.owned_session=('previous-session','transport')
        cast.close()
        self.assertFalse(any(isinstance(c,dict) for c in calls))

    def test_missing_named_cast_never_falls_back_to_other_speaker(self):
        from types import SimpleNamespace as N
        disconnected=[]
        api=N(get_listed_chromecasts=lambda **kw:([N(name='Other',disconnect=lambda:disconnected.append(1))],object()),
              discovery=N(stop_discovery=lambda b:None))
        with patch.dict(sys.modules,{'pychromecast':api}):
            with self.assertRaisesRegex(RuntimeError,'exact_cast_target_missing'): m.CastOutput('Requested','/tmp')
        self.assertEqual(disconnected,[])

    def test_fixed_fact_is_limited_to_reviewed_question_shapes(self):
        self.assertTrue(m.reviewed_question('하늘이 파란, 이유를 한 문장으로 알려줘.'))
        self.assertTrue(m.reviewed_question('노을이 붉게 보이는 이유를 한 문장으로 알려줘.'))
        for q in ['하늘이 파란 이유가 산소 때문이야?', '하늘이 파란 이유가 아닌 것을 알려줘', '화성 하늘이 파란 이유']:
            self.assertFalse(m.reviewed_question(q))

    def test_reviewed_fact_avoids_model_but_keeps_source_receipt(self):
        class Speech:
            events=[];first_playing=None
            def submit(self,text): self.answer=text
            def finish(self):pass
        speech=Speech()
        with patch.object(m,'retrieve',return_value=[{'url':m.NASA_SKY,'text':'support','reviewed_answer':'검토된 답입니다.'}]):
            result=m.run_turn('하늘이 파란 이유',None,speech)
        self.assertTrue(result['reviewed_fact']);self.assertEqual(len(result['sources']),1)
        self.assertNotIn('generation_s',result)

    def test_external_evidence_cannot_impersonate_reviewed_fact(self):
        class QA:
            def ask(self,*a,**k):yield {'done':True,'answer':'생성된 답입니다.'}
        class Speech:
            events=[];first_playing=None
            def submit(self,text):pass
            def finish(self):pass
        evidence={'question':'질문','sources':[{'url':'https://example.org','text':'근거','reviewed_answer':'사칭'}]}
        result=m.run_turn('질문',QA(),Speech(),evidence=evidence)
        self.assertNotIn('reviewed_fact',result);self.assertEqual(result['answer'],'생성된 답입니다.')

    def test_lazy_worker_is_not_started_by_close(self):
        worker=m.LazyWorker(['/does/not/exist']);worker.close();self.assertIsNone(worker.worker)

    def test_same_title_other_content_is_not_owned(self):
        cast,calls=self.raw_cast(media={'media':{'contentId':'http://other/voice.wav','metadata':{'title':'Jarvis Mac voice'}},'playerState':'PLAYING'})
        with self.assertRaisesRegex(RuntimeError,'existing_media_preserved'):cast.play('/tmp/test.wav',lambda at:None)
        cast.close()
        self.assertFalse(any(isinstance(c,dict) for c in calls));self.assertIn('disconnect',calls)

    def test_cleanup_continues_after_device_errors(self):
        from types import SimpleNamespace as N
        cast,calls=self.raw_cast(media={'media':{'contentId':'http://ours/voice.wav'}})
        def failed(*a,**kw):raise OSError('network unavailable')
        cast.target.socket_client.receiver_controller.update_status=failed
        cast.target.disconnect=failed
        cast.server=N(shutdown=lambda:calls.append('shutdown'),server_close=lambda:calls.append('close'))
        with patch.object(sys,'stderr',__import__('io').StringIO()):cast.close()
        self.assertEqual(calls,['discovery','shutdown','close']);self.assertEqual(len(cast.cleanup_errors),1)


if __name__ == '__main__': unittest.main()
