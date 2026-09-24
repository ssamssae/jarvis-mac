import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_cursor_acp import CursorACP
from jarvis_cursor_qa import CursorQA

SERVER = r'''
import json, os, pathlib, sys, time
config=json.loads((pathlib.Path(os.environ['CURSOR_CONFIG_DIR'])/'cli-config.json').read_text())
assert config['model']['modelId']=='same-model'
assert config['selectedModel']['parameters']==[{'id':'effort','value':'high'}]
assert not config['permissions']['allow']
assert 'Shell(*)' in config['permissions']['deny']
assert 'Mcp(*:*)' in config['permissions']['deny']
assert '--model' not in sys.argv and '--resume' not in sys.argv
assert sys.argv[sys.argv.index('--mode')+1]=='ask'
assert 'CURSOR_API_KEY' not in os.environ
count=0
def send(m): print(json.dumps(dict(jsonrpc='2.0',**m)),flush=True)
def chunk(session, text, kind='agent_message_chunk'):
 send({'method':'session/update','params':{'sessionId':session,'update':{'sessionUpdate':kind,'content':{'type':'text','text':text}}}})
for line in sys.stdin:
 m=json.loads(line);method=m['method'];rid=m['id'];p=m['params']
 if method=='initialize':
  assert p['clientCapabilities']=={'fs':{'readTextFile':False,'writeTextFile':False},'terminal':False}
  result={'protocolVersion':1}
 elif method=='authenticate':
  assert p['methodId']=='cursor_login';result={}
 elif method=='session/new':
  count+=1;assert not p['mcpServers'];result={'sessionId':'s'+str(count)}
 elif method=='session/set_mode':
  assert p['modeId']=='ask';result={}
 elif method=='session/prompt':
  sid=p['sessionId']
  if scenario=='malformed':print('not-json',flush=True);continue
  if scenario=='model_error':
   send({'id':rid,'error':{'code':-1,'message':'Unknown model ID: secret-provider-detail'}});continue
  if scenario=='permissions':
   send({'id':'permission','method':'session/request_permission','params':{'options':[{'kind':'allow_once','optionId':'allow'},{'kind':'reject_once','optionId':'reject'}]}})
   response=json.loads(sys.stdin.readline());assert response['result']['outcome']=={'outcome':'selected','optionId':'reject'}
   send({'id':'read','method':'fs/read_text_file','params':{'path':'/private'}})
   response=json.loads(sys.stdin.readline());assert response['error']['code']==-32601
  chunk('unrelated','never speak this')
  chunk(sid,'private reasoning','agent_thought_chunk')
  chunk(sid,('\n  ' if scenario=='whitespace' else '')+'첫 문장. ')
  if scenario=='timeout':time.sleep(10)
  if scenario=='disconnect':sys.exit(1)
  if scenario=='mode_change':
   send({'method':'session/update','params':{'sessionId':sid,'update':{'sessionUpdate':'current_mode_update','currentModeId':'agent'}}})
  time.sleep(.08)
  chunk(sid,'둘째 문장.')
  result={'stopReason':'max_tokens' if scenario=='incomplete' else 'end_turn'}
 else: raise AssertionError(method)
 send({'id':rid,'result':result})
'''


class ACPTests(unittest.TestCase):
    def fixture(self, root, scenario='normal', timeout=3):
        config=root/'original';config.mkdir()
        source=config/'cli-config.json'
        source.write_text(json.dumps({'model':{'modelId':'same-model'},'selectedModel':{
            'modelId':'same-model','parameters':[{'id':'effort','value':'high'}]}}))
        binary=root/'agent';binary.write_text('#!'+sys.executable+'\nscenario='+repr(scenario)+'\n'+SERVER)
        binary.chmod(0o700)
        return CursorACP(binary,config,timeout),source

    def test_stream_is_incremental_and_process_reused_in_isolated_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,source=self.fixture(Path(tmp));before=source.read_bytes()
            try:
                events=worker.ask('질문',stream=True);first=next(events);pid=worker.process.pid
                self.assertEqual(first,{'snapshot':'첫 문장. ','done':False})
                self.assertIsNone(worker.process.poll())
                rest=list(events);self.assertEqual(rest[-1]['answer'],'첫 문장. 둘째 문장.')
                self.assertNotIn('private reasoning',json.dumps(rest))
                self.assertFalse(rest[-1]['timings']['process_reused'])
                again=list(worker.ask('다음',stream=True))
                self.assertEqual(worker.process.pid,pid)
                self.assertTrue(again[-1]['timings']['process_reused'])
                self.assertEqual(source.read_bytes(),before)
                directory=worker.directory.name
            finally:worker.close()
            self.assertFalse(Path(directory).exists())
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_permissions_denied_and_unadvertised_client_requests_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,_=self.fixture(Path(tmp),'permissions')
            try:self.assertEqual(list(worker.ask('질문'))[-1]['answer'],'첫 문장. 둘째 문장.')
            finally:worker.close()

    def test_whitespace_is_consistent_between_snapshots_and_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,_=self.fixture(Path(tmp),'whitespace')
            try:
                events=list(worker.ask('질문',stream=True))
                self.assertEqual(events[0]['snapshot'],'첫 문장. ')
                self.assertTrue(events[-1]['answer'].startswith(events[0]['snapshot']))
            finally:worker.close()

    def test_failed_process_is_recreated_on_next_request_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,_=self.fixture(Path(tmp),'disconnect')
            with self.assertRaisesRegex(RuntimeError,'disconnected'):list(worker.ask('실패'))
            worker.binary.write_text('#!'+sys.executable+'\nscenario="normal"\n'+SERVER)
            try:
                result=list(worker.ask('새 질문'))[-1]
                self.assertFalse(result['timings']['process_reused'])
                self.assertEqual(result['answer'],'첫 문장. 둘째 문장.')
            finally:worker.close()

    def test_session_limit_recycles_owned_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,_=self.fixture(Path(tmp))
            try:
                list(worker.ask('질문'));pid=worker.process.pid;worker.sessions=32
                result=list(worker.ask('새 질문'))[-1]
                self.assertFalse(result['timings']['process_reused'])
                self.assertNotEqual(worker.process.pid,pid)
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            finally:worker.close()

    def test_failures_close_only_owned_process_and_never_yield_success(self):
        for scenario,error in [('timeout','cursor_timeout'),('disconnect','cursor_acp_disconnected'),
            ('incomplete','cursor_no_success_result'),('mode_change','cursor_acp_mode_changed'),
            ('malformed','cursor_invalid_json'),('model_error','cursor_model_unavailable')]:
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as tmp:
                worker,_=self.fixture(Path(tmp),scenario,timeout=3)
                events=[]
                with self.assertRaisesRegex((RuntimeError,TimeoutError),error):
                    for e in worker.ask('질문',stream=True):events.append(e)
                self.assertFalse(any(e.get('done') for e in events))
                self.assertIsNone(worker.process);self.assertIsNone(worker.directory)

    def test_consumer_cancellation_closes_transport_without_memory_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,source=self.fixture(Path(tmp))
            qa=CursorQA(worker.binary,source.parent,persistent=True)
            events=qa.ask_conversation('질문',stream=True);next(events);pid=qa.acp.process.pid
            events.close()
            self.assertEqual(qa.history,[]);self.assertIsNone(qa.acp.process)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            qa.close()

    def test_conversation_remembers_success_and_close_releases_persistent_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,source=self.fixture(Path(tmp));qa=CursorQA(worker.binary,source.parent,persistent=True)
            try:
                list(qa.ask_conversation('첫 질문',stream=True));pid=qa.acp.process.pid
                list(qa.ask_conversation('둘째 질문',stream=True))
                self.assertEqual(qa.acp.process.pid,pid);self.assertEqual(len(qa.history),4)
            finally:qa.close()
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)


if __name__=='__main__':unittest.main()
