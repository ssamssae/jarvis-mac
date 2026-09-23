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
    def test_final_success_only(self):
        result=m.parse_result(0,json.dumps({'type':'result','subtype':'success','is_error':False,'result':'답입니다.'}), '')
        self.assertEqual(result['answer'],'답입니다.');self.assertEqual(result['backend'],'cursor')
        for record in [{'type':'assistant','result':'중간 답'}, {'type':'result','subtype':'error','is_error':True,'result':'bad'}, []]:
            with self.assertRaisesRegex(RuntimeError,'success'): m.parse_result(0,json.dumps(record),'')

    def test_progress_text_is_not_spoken(self):
        with self.assertRaisesRegex(RuntimeError,'invalid_json'):m.parse_result(0,'Read file\nWorking...', '')
        with self.assertRaisesRegex(RuntimeError,'invalid_answer'):m.parse_result(0,json.dumps({'type':'result','subtype':'success','is_error':False,'result':''}),'')

    def test_error_output_is_redacted(self):
        with self.assertRaisesRegex(RuntimeError,'^cursor_keychain_locked$'):
            m.parse_result(1,'','Error: keychain is locked; secret data')
        with self.assertRaisesRegex(RuntimeError,'^cursor_failed_exit_3$'):
            m.parse_result(3,'private provider error','private context')

    def test_no_model_override_resume_api_key_or_force(self):
        args=m.cursor_command('/agent','/workspace')
        for flag in ['--model','--resume','--api-key','--force','--yolo']:self.assertNotIn(flag,args)
        self.assertEqual(args[args.index('--mode')+1],'ask')
        self.assertEqual(args[args.index('--output-format')+1],'json')

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
