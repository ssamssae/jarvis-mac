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
    def test_matter_only_starts_then_local_microphone_selects_and_dictates(self):
        self.check_flow([None, None, '코덱스', '노트북', None, '승인', '', '엔터'],
                        ['armed', 'armed', 'armed', 'dictating', 'dictating', 'dictating', 'dictating'], guided=True)
    def test_named_target_multisegment_literal_approval_and_single_enter(self):
        self.check_named_target('자비스 헤르메스 코덱스')

    def test_english_target_enters_dictation_without_conversation(self):
        self.check_named_target('자비스 Hermes Codex')

    def check_named_target(self, invocation):
        self.check_flow([invocation, '승인', '', '엔터'],
                        ['dictating', 'dictating', 'dictating'])

    def test_guided_notebook_flow_and_unknown_replies_stay_local(self):
        self.check_flow(['자비스', '음성 입력', '알 수 없는 엔진', '코덱스',
                         '페르멘스', '노트북', '승인', '', '엔터'],
                        ['armed', 'armed', 'armed', 'armed', 'armed',
                         'dictating', 'dictating', 'dictating'], guided=True)

    def test_prompt_started_timeout_preserves_selection(self):
        self.check_flow(['자비스 음성 입력', '코덱스', '노트북', '승인', '', '엔터'],
                        ['armed', 'armed', 'dictating', 'dictating', 'dictating'],
                        guided=True, prompt_timeout=True)

    def test_guided_cancel_does_not_send_or_call_conversation(self):
        self.check_flow(['자비스 음성 입력', '코덱스', '취소'],
                        ['armed', 'armed'], expected_delivery=False)

    def test_native_worker_hints_follow_selection_and_stop_before_body(self):
        self.check_flow(['자비스 음성 입력', '코덱스', '노트북', '승인', '', '엔터'],
                        ['armed', 'armed', 'dictating', 'dictating', 'dictating'],
                        guided=True, custom_worker=False)

    def test_timeout_before_prompt_started_cancels_selection(self):
        self.check_flow(['자비스 음성 입력'], [], prompt_timeout=True,
                        playback_started=False, expected_delivery=False)

    def check_flow(self, transcripts, expected_states, guided=False, prompt_timeout=False,
                   playback_started=True, expected_delivery=True, custom_worker=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.json').write_text(json.dumps({'cast_name':'test', 'stt_worker':'test',
                                                       'dictation':{'argv':['test']}}))
            if not custom_worker:
                config = json.loads((root/'config.json').read_text())
                config.pop('stt_worker')
                (root/'config.json').write_text(json.dumps(config))
            cast = Mock(directory=directory, cast=None, startup_prepare_s=0,
                        startup_prepare_error=None)
            speech = Mock(first_playing=None, ack_first_playing=None, events=[])
            if prompt_timeout:
                speech.first_playing = time.monotonic() if playback_started else None
                speech.finish.side_effect = TimeoutError('cast_playback_timeout')
            indicator = Mock()
            stt = Mock()
            stt.read.side_effect = [{'text':s} for s in transcripts if s is not None]
            requests = []
            final_states = []
            def deliver(config, request):
                requests.append(request)
                return {'status':'submitted'}
            def events(*args):
                for index in range(len(transcripts)):
                    audio = root/'audio'/f'{index}.wav'
                    audio.write_bytes(b'x'*44)
                    now = time.time()
                    event = dict(wav=str(audio), speech_started_wall=now-2,
                                 speech_ended_wall=now-1, capture_ended_wall=now)
                    if index:
                        state = json.loads((root/'status.json').read_text())
                        self.assertTrue(state['listen'])
                        self.assertEqual(state['state'], expected_states[index-1])
                        event['dictation_id'] = state.get('dictation_id', '')
                    if transcripts[index] is None:
                        event = dict(source='google-home-matter', intent='dictation_start',
                                     speech_started_wall=now, speech_ended_wall=now, capture_ended_wall=now)
                    yield event
                final_states.append(json.loads((root/'status.json').read_text()))
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
            qa.assert_not_called()
            contexts = [call.args[0].get('selection_context') for call in stt.send.call_args_list]
            if not custom_worker:
                self.assertEqual(contexts, [None, 'engine', 'node', None, None, None])
            else:
                self.assertTrue(all(context is None for context in contexts))
            if not expected_delivery:
                self.assertEqual(requests, [])
                self.assertEqual(final_states[0]['state'], 'listening')
                return
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['text'], '승인')
            self.assertEqual(requests[0]['node'], 'macbook14')
            self.assertEqual(requests[0]['engine'], 'codex')
            qa.assert_not_called()
            indicator.show.assert_any_call('green', ttl=0)
            indicator.show.assert_any_call('yellow', ttl=1.5)
            self.assertIn(('말씀하세요.',), [call.args for call in speech.submit.call_args_list])
            if guided:
                self.assertIn(('어디로 연결할까요?',), [call.args for call in speech.submit.call_args_list])
                self.assertIn(('어떤 노드인가요?',), [call.args for call in speech.submit.call_args_list])
            self.assertEqual(list((root/'pending-dictations').glob('*.json')), [])
