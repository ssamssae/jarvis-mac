import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis_mac_listener as listener


class ListenerDictationTest(unittest.TestCase):
    def test_named_target_multisegment_literal_approval_and_single_enter(self):
        self.check_named_target('자비스 헤르메스 코덱스')

    def test_english_target_enters_dictation_without_conversation(self):
        self.check_named_target('자비스 Hermes Codex')

    def check_named_target(self, invocation):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.json').write_text(json.dumps({'cast_name':'test', 'stt_worker':'test',
                                                       'dictation':{'argv':['test']}}))
            cast = Mock(directory=directory, cast=None, startup_prepare_s=0,
                        startup_prepare_error=None)
            speech = Mock(first_playing=None, ack_first_playing=None, events=[])
            indicator = Mock()
            stt = Mock()
            stt.read.side_effect = [{'text':s} for s in [invocation, '승인', '', '엔터']]
            requests = []
            def deliver(config, request):
                requests.append(request)
                return {'status':'submitted'}
            def events(*args):
                for index in range(4):
                    audio = root/'audio'/f'{index}.wav'
                    audio.write_bytes(b'x'*44)
                    now = time.time()
                    event = dict(wav=str(audio), speech_started_wall=now-2,
                                 speech_ended_wall=now-1, capture_ended_wall=now)
                    if index:
                        state = json.loads((root/'status.json').read_text())
                        self.assertTrue(state['listen'])
                        self.assertEqual(state['state'], 'dictating')
                        event['dictation_id'] = state['dictation_id']
                    yield event
            with patch.object(sys, 'argv', ['listener', '--state-dir', directory]), \
                 patch.object(listener, 'JSONWorker', return_value=stt), \
                 patch.object(listener, 'worker_command', return_value=['test']), \
                 patch.object(listener, 'CastSession', return_value=cast), \
                 patch.object(listener, 'SpeechQueue', return_value=speech), \
                 patch.object(listener, 'StatusLight', return_value=indicator), \
                 patch.object(listener, 'control_events', events), \
                 patch.object(listener, 'deliver', deliver), \
                 patch.object(listener, 'CursorQA') as qa, \
                 patch.object(listener.time, 'sleep'), \
                 patch.object(listener.signal, 'signal'), \
                 patch('builtins.print'):
                listener.main()
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['text'], '승인')
            self.assertEqual(requests[0]['node'], 'macbook14')
            self.assertEqual(requests[0]['engine'], 'codex')
            qa.assert_not_called()
            indicator.show.assert_any_call('green', ttl=0)
            indicator.show.assert_any_call('yellow', ttl=1.5)
            self.assertIn(('말씀하세요.',), [call.args for call in speech.submit.call_args_list])
            self.assertEqual(list((root/'pending-dictations').glob('*.json')), [])
