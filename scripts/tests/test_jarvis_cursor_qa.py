import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('jarvis_cursor_qa',ROOT/'jarvis_cursor_qa.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class CursorTests(unittest.TestCase):
    def test_conversation_keeps_successful_pairs_and_uses_general_instructions(self):
        worker=m.CursorQA()
        answers=[[{'done':True,'answer':'안녕하세요.'}], [{'done':True,'answer':'파랑입니다.'}]]
        with patch.object(worker,'ask',side_effect=answers) as ask:
            list(worker.ask_conversation('안녕. 내 색상은 파랑이야'))
            list(worker.ask_conversation('내 색상은 뭐야?'))
        messages=json.loads(ask.call_args.args[0])
        self.assertEqual([x['role'] for x in messages],['user','assistant','user'])
        self.assertIn('파랑',messages[0]['content'])
        self.assertEqual(ask.call_args.kwargs['instructions'],m.CONVERSATION_INSTRUCTIONS)
        self.assertEqual(len(worker.history),4)

    def test_failed_turn_does_not_poison_context_and_new_instance_is_empty(self):
        worker=m.CursorQA()
        with patch.object(worker,'ask',return_value=iter([{'done':True,'answer':'정상'}])):
            list(worker.ask_conversation('첫 질문'))
        before=worker.history[:]
        with patch.object(worker,'ask',side_effect=RuntimeError('cursor_timeout')):
            with self.assertRaises(RuntimeError):list(worker.ask_conversation('실패한 질문'))
        self.assertEqual(worker.history,before)
        self.assertEqual(m.CursorQA().history,[])

    def test_conversation_history_is_bounded_in_complete_pairs(self):
        worker=m.CursorQA()
        with patch.object(worker,'ask',side_effect=lambda *a,**k: iter([{'done':True,'answer':'짧은 답'}])):
            for i in range(10):list(worker.ask_conversation(str(i)))
        self.assertEqual(len(worker.history),12)
        self.assertEqual(worker.history[0]['content'],'4')
        with patch.object(worker,'ask',return_value=iter([{'done':True,'answer':'긴'*7000}])):
            list(worker.ask_conversation('질문'*1500))
        self.assertLessEqual(len(json.dumps(worker.history,ensure_ascii=False)),10000)
        self.assertEqual(len(worker.history)%2,0)
        with self.assertRaises(ValueError):list(worker.ask_conversation('x'*4001))

    def test_final_success_only(self):
        result=m.parse_result(0,json.dumps({'type':'result','subtype':'success','is_error':False,'result':'답입니다.'}), '')
        self.assertEqual(result['answer'],'답입니다.');self.assertEqual(result['backend'],'cursor')
        for record in [{'type':'assistant','result':'중간 답'}, {'type':'result','subtype':'error','is_error':True,'result':'bad'}, []]:
            with self.assertRaisesRegex(RuntimeError,'success'): m.parse_result(0,json.dumps(record),'')

    def test_progress_text_is_not_spoken(self):
        with self.assertRaisesRegex(RuntimeError,'invalid_json'):m.parse_result(0,'Read file\nWorking...', '')
        with self.assertRaisesRegex(RuntimeError,'invalid_answer'):m.parse_result(0,json.dumps({'type':'result','subtype':'success','is_error':False,'result':''}),'')

    def test_error_output_is_redacted(self):
        with self.assertRaisesRegex(RuntimeError,'^cursor_model_unavailable$'):
            m.parse_result(1,'','ActionRequiredError: AI Model Not Found Unknown model ID: private-model-id')
        with self.assertRaisesRegex(RuntimeError,'^cursor_keychain_locked$'):
            m.parse_result(1,'','Error: keychain is locked; secret data')
        with self.assertRaisesRegex(RuntimeError,'^cursor_failed_exit_3$'):
            m.parse_result(3,'private provider error','private context')

    def test_no_model_override_resume_api_key_or_force(self):
        args=m.cursor_command('/agent','/workspace')
        for flag in ['--model','--resume','--api-key','--force','--yolo']:self.assertNotIn(flag,args)
        self.assertEqual(args[args.index('--mode')+1],'ask')
        self.assertEqual(args[args.index('--output-format')+1],'stream-json')
        self.assertIn('--stream-partial-output',args)

    def fake(self,root,body):
        exe=root/'agent';exe.write_text('#!/usr/bin/env python3\n'+body);exe.chmod(0o700)
        config=root/'config';config.mkdir();source=config/'cli-config.json'
        source.write_text(json.dumps({'version':1,'model':{'modelId':'existing-model'},'modelParameters':{'effort':'high'},'permissions':{'allow':['Shell(*)']}}))
        return exe,config,source

    def test_isolation_preserves_model_and_source_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            exe,config,source=self.fake(root,'''import os,pathlib,json,sys
config=pathlib.Path(os.environ['CURSOR_CONFIG_DIR'])/'cli-config.json'
data=json.loads(config.read_text())
assert data['model']['modelId']=='existing-model'
assert data['modelParameters']['effort']=='high'
assert 'Shell(*)' in data['permissions']['deny']
assert 'Mcp(*:*)' in data['permissions']['deny']
assert 'CURSOR_API_KEY' not in os.environ
assert 'CURSOR_AUTH_TOKEN' not in os.environ
assert '질문' in sys.stdin.read()
assert '--model' not in sys.argv
config.write_text('changed isolated copy')
print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'검증 답변'}))
''')
            before=source.read_bytes();worker=m.CursorQA(exe,config)
            with patch.dict(os.environ,{'CURSOR_API_KEY':'never-forward','CURSOR_AUTH_TOKEN':'never-forward'}):
                events=list(worker.ask('질문'))
            self.assertEqual(events[0]['answer'],'검증 답변');self.assertEqual(source.read_bytes(),before)
            self.assertIsNone(worker.process)

    def test_timeout_cleans_owned_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe,config,_=self.fake(pathlib.Path(tmp),'import time\ntime.sleep(20)\n')
            worker=m.CursorQA(exe,config,timeout=.1)
            with self.assertRaisesRegex(TimeoutError,'cursor_timeout'):list(worker.ask('질문'))
            self.assertIsNone(worker.process)

    def test_missing_selected_model_is_not_silently_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);(root/'cli-config.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'selected_model_missing'):
                list(m.CursorQA('/nonexistent',root).ask('질문'))

    def run_records(self, records, ending='', timeout=3):
        with tempfile.TemporaryDirectory() as tmp:
            body = 'import json,sys,time\nsys.stdin.read()\n'
            for record in records:
                body += 'print('+repr(json.dumps(record))+',flush=True)\ntime.sleep(.02)\n'
            exe,config,_=self.fake(pathlib.Path(tmp),body+ending)
            worker=m.CursorQA(exe,config,timeout=timeout)
            try: return list(worker.ask('질문'))
            finally: self.assertIsNone(worker.process)

    def test_stream_observes_delta_not_duplicate_flush_and_only_yields_final(self):
        records=[{'type':'system','subtype':'init','session_id':'do-not-return'},
                 {'type':'assistant','timestamp_ms':1,'model_call_id':'duplicate',
                  'message':{'content':[{'type':'text','text':'never spoken'}]}},
                 {'type':'assistant','message':{'content':[{'type':'text','text':'final flush'}]}},
                 {'type':'assistant','timestamp_ms':2,'message':{'content':[{'type':'text','text':'partial'}]}},
                 {'type':'result','subtype':'success','is_error':False,'result':'완성 답변','duration_api_ms':10}]
        events=self.run_records(records)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['answer'],'완성 답변')
        timings=events[0]['timings']
        self.assertLess(timings['init_s'],timings['first_delta_s'])
        self.assertGreater(timings['first_delta_s']-timings['init_s'],.04)
        self.assertLessEqual(timings['first_delta_s'],timings['result_s'])
        self.assertLessEqual(timings['result_s'],timings['process_exit_s'])
        self.assertNotIn('do-not-return',json.dumps(events));self.assertNotIn('partial',json.dumps(events))

    def test_missing_duplicate_or_error_terminal_never_yields_answer(self):
        success={'type':'result','subtype':'success','is_error':False,'result':'답변'}
        for records in [[],[success,success],[dict(success,subtype='error',is_error=True)]]:
            with self.assertRaisesRegex(RuntimeError,'no_success_result'):self.run_records(records)
        with self.assertRaisesRegex(RuntimeError,'cursor_failed_exit_7'):
            self.run_records([success], 'sys.exit(7)\n')

    def test_stream_malformed_or_oversized_output_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'cursor_invalid_json'):
            self.run_records([], 'print("private non-json diagnostic")\n')
        with self.assertRaisesRegex(RuntimeError,'cursor_output_too_large'):
            self.run_records([], 'sys.stderr.write("x"*1000001);sys.stderr.flush();time.sleep(20)\n')

    def test_success_result_without_process_exit_still_times_out(self):
        with self.assertRaisesRegex(TimeoutError,'cursor_timeout'):
            self.run_records([{'type':'result','subtype':'success','is_error':False,'result':'not yet safe'}],
                             'time.sleep(20)\n', timeout=.2)

    def test_duplicate_only_stream_has_no_first_delta(self):
        events=self.run_records([
            {'type':'assistant','timestamp_ms':1,'model_call_id':'buffered',
             'message':{'content':[{'type':'text','text':'duplicate'}]}},
            {'type':'result','subtype':'success','is_error':False,'result':'완성 답변'}])
        self.assertIsNone(events[0]['timings']['first_delta_s'])

    def test_unsupported_options_are_rejected(self):
        worker=m.CursorQA('/nonexistent')
        with self.assertRaisesRegex(ValueError,'prewarm_not_supported'):worker.prepare()
        with self.assertRaisesRegex(ValueError,'streaming_not_enabled'):list(worker.ask('질문',stream=True))

    def test_parent_exit_and_term_ignoring_child_are_cleaned(self):
        import subprocess,time
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); pidfile=root/'child.pid'
            child="import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("+repr(str(pidfile))+").write_text(str(os.getpid())); time.sleep(20)"
            body="import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',"+repr(child)+"])\ntime.sleep(.05)\n"
            exe,config,_=self.fake(root,body)
            worker=m.CursorQA(exe,config,timeout=2)
            with self.assertRaisesRegex(TimeoutError,'cursor_timeout'):list(worker.ask('질문'))
            self.assertIsNone(worker.process);self.assertTrue(pidfile.exists())
            deadline=time.monotonic()+1
            while True:
                status=subprocess.run(['ps','-p',pidfile.read_text(),'-o','stat='],capture_output=True,text=True).stdout.strip()
                if not status or status.startswith('Z') or time.monotonic()>deadline:break
                time.sleep(.02)
            self.assertTrue(not status or status.startswith('Z'),status)

if __name__=='__main__':unittest.main()
