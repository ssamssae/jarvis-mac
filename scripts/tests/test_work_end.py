from pathlib import Path
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis_work_end as end


class ConfirmationTests(unittest.TestCase):
    def test_japanese_confirmation_only_after_prompt_and_one_use(self):
        c = end.Confirmation()
        self.assertIsNone(c.accept('はい', 1, 1))
        c.arm(10, 100)
        self.assertEqual(c.accept('はい', 11, 101), 'cancel')
        for word in ['はい', 'はい。']:
            c.arm(10, 100, japanese=True)
            self.assertEqual(c.accept(word, 11, 101), 'execute')
            self.assertIsNone(c.accept(word, 12, 102))
        for word, now, wall in [('はい', 22, 101), ('はい', 11, 99), ('はい、いいえ', 11, 101), ('いいえ', 11, 101), ('네', 11, 101)]:
            c.arm(10, 100, japanese=True)
            self.assertNotEqual(c.accept(word, now, wall), 'execute')

    def test_request_never_executes_and_bare_yes_never_executes(self):
        c = end.Confirmation()
        self.assertIsNone(c.accept('예', 1, 1))
        for word in ['시고토 오와리', '시고토오와리!', 'しごとおわり', 'しごと終わり', '仕事 終わり', '시골토의 끝', '직업 끝']:
            self.assertEqual(c.accept(word, 2, 2), 'prompt')
        self.assertIsNone(c.accept('예', 3, 3))  # arm only after successful question playback
        for word in ['시골토의 끝이라고 말해', '직업 끝내지마', '직업 끝 예', '일 끝', '끝!', '시고토 오와리 하지마', '시고토 오와리 예', '시고토 오와리라고 말해', '일 끝내지마', '일 끝이라고 말해', '일 끝 예', '끝내지마', '회의 끝', '끝 예']:
            self.assertIsNone(c.accept(word, 3, 3))

    def test_yes_once_only_after_prompt_and_before_deadline(self):
        for word in ['예', '네.']:
            c = end.Confirmation(); c.arm(10, 100)
            self.assertEqual(c.accept(word, 11, 101), 'execute')
            self.assertIsNone(c.accept(word, 12, 102))
        for now, wall in [(22, 101), (23, 101), (11, 99)]:
            c = end.Confirmation(); c.arm(10, 100)
            self.assertNotEqual(c.accept('예', now, wall), 'execute')

    def test_negative_or_unrelated_reply_cancels(self):
        for word in ['아니요', '취소', '예 하지마', '오늘 날씨']:
            c = end.Confirmation(); c.arm(10, 100)
            self.assertEqual(c.accept(word, 11, 101), 'cancel')
            self.assertIsNone(c.accept('예', 12, 102))

    def test_cancel_is_local_even_without_pending_confirmation(self):
        for text in ['취소', '취소 취소', '치솔 치솔', '아니요', '취소해줘']:
            c = end.Confirmation()
            self.assertEqual(c.accept(text, 100, 100), 'cancel')
            self.assertIsNone(c.accept('예', 101, 101))

    def test_execution_receipts_and_partial_failure(self):
        cfg = {'steps': [{'name': 'Mac', 'kind': 'brightness', 'argv': ['brightness', '--level', '0']}] * 2 + [{'name': 'PC', 'kind': 'shutdown', 'argv': ['ssh', 'pc', 'shutdown-helper']}] * 2}
        def runner(argv, **kw):
            self.assertNotIn('shell', kw)
            return subprocess.CompletedProcess(argv, 0, json.dumps({'verified': True, 'level': 0}) if argv[0] == 'brightness' else 'JARVIS_SHUTDOWN_ACCEPTED', '')
        result = end.execute(cfg, runner)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual('작전 종료 절차를 시작합니다.', result['answer'])
        self.assertNotIn('종료했', result['answer'])
        run = Mock(return_value=subprocess.CompletedProcess([], 255, '', ''))
        failed = end.execute(cfg, run)
        self.assertEqual(failed['status'], 'partial')
        self.assertEqual(failed['answer'], '작전 종료 절차를 시작합니다.')
        self.assertEqual([step['status'] for step in failed['steps']], ['unverified'] * 4)
        self.assertEqual(end.execute({'steps': []}, run)['status'], 'unconfigured')


class RoutingTests(unittest.TestCase):
    def run_dialog(self, texts, speech_error=False, google=False, expected_languages=None, window=8, start_expected=0):
        import jarvis_mac_listener as app
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'audio').mkdir()
            (root / 'config.json').write_text(json.dumps({'cast_name': 'fixture', 'model': 'fixture', 'work_end': {'steps': []}}))
            def events():
                for i in range(len(texts)):
                    if google and i == 0:
                        now = time.time()
                        yield json.dumps({'source': 'google-home-matter', 'intent': 'work_end', 'speech_started_wall': now, 'speech_ended_wall': now, 'capture_ended_wall': now}) + '\n'
                        continue
                    clip = root / 'audio' / f'{i}.wav'; clip.write_bytes(b'x'*44)
                    now = time.time()
                    command_audio = i == 0 or (expected_languages and expected_languages[i] == 'ja')
                    yield json.dumps({'wav': str(clip), 'speech_started_wall': now - 3 if command_audio else now, 'speech_ended_wall': now, 'capture_ended_wall': now}) + '\n'
            stt = MagicMock(); stt.read.side_effect = [x if isinstance(x, Exception) else {'text': x} for x in (texts[1:] if google else texts)]
            home = MagicMock(); home.plan.return_value = None
            speech = MagicMock(); speech.first_playing = None; speech.ack_first_playing = None; speech.events = []
            if speech_error: speech.finish.side_effect = RuntimeError('speaker_failed')
            indicator = MagicMock(); indicator.release.return_value = True
            with patch.object(app, 'WakeGate', return_value=app.WakeGate(window=window)), patch.object(app.jarvis_work_mode, 'execute', return_value={'status':'ok','answer':'시작 요청'}) as start_work, patch.object(sys, 'argv', ['listener', '--state-dir', str(root)]), patch.object(sys, 'stdin', events()), patch.object(sys, 'stdout', io.StringIO()), patch.object(app.signal, 'signal'), patch.object(app, 'SmartHome', return_value=home), patch.object(app, 'JSONWorker', return_value=stt), patch.object(app, 'CastOutput'), patch.object(app, 'SpeechQueue', return_value=speech), patch.object(app, 'StatusLight', return_value=indicator), patch.object(app, 'CursorQA') as qa, patch.object(app.jarvis_work_end, 'execute', return_value={'status': 'ok', 'answer': '종료를 요청했어요.'}) as execute:
                app.main()
            qa.assert_not_called(); home.execute.assert_not_called()
            self.assertEqual(start_work.call_count, start_expected)
            if google and len(texts) > 1 and not speech_error:
                self.assertEqual(stt.send.call_args_list[0].args[0].get('language'), 'ja')
            if expected_languages is not None:
                self.assertEqual([call.args[0].get('language') for call in stt.send.call_args_list], expected_languages)
            return execute.call_count

    def test_explicit_japanese_trigger_returns_to_korean_confirmation(self):
        for yes in ['예', '네']:
            self.assertEqual(self.run_dialog(['자비스 일본어', '仕事終わり', yes, yes],
                                            expected_languages=[None, 'ja', None, None]), 1)
        self.assertEqual(self.run_dialog(['자비스 일본어', '仕事終わり', '하이', '예'],
                                        expected_languages=[None, 'ja', None, None]), 0)
        self.assertEqual(self.run_dialog(['자비스 일본어', '仕事終わり', 'はい', '예'],
                                        expected_languages=[None, 'ja', None, None]), 0)

    def test_japanese_mode_start_and_expiry(self):
        self.assertEqual(self.run_dialog(['자비스 일본어', '仕事スタート'],
                                        expected_languages=[None, 'ja'], start_expected=1), 0)
        self.assertEqual(self.run_dialog(['자비스 일본어', '仕事終わり'], window=0,
                                        expected_languages=[None, None]), 0)

    def test_japanese_trigger_is_wake_qualified_and_one_utterance(self):
        self.assertEqual(self.run_dialog(['일본어', '仕事終わり'], expected_languages=[None, None]), 0)
        self.assertEqual(self.run_dialog(['자비스', '일본어', '仕事終わり', '아니요'],
                                        expected_languages=[None, None, 'ja', None]), 0)
        self.assertEqual(self.run_dialog(['자비스 일본어', '今日の天気', '仕事終わり'],
                                        expected_languages=[None, 'ja', None]), 0)
        self.assertEqual(self.run_dialog(['자비스 일본어', '仕事終わり'], speech_error=True,
                                        expected_languages=[None, None]), 0)

    def test_google_request_only_arms_and_hai_executes_once(self):
        self.assertEqual(self.run_dialog(['request'], google=True), 0)
        self.assertEqual(self.run_dialog(['request', 'はい', 'はい'], google=True), 1)
        self.assertEqual(self.run_dialog(['request', 'いいえ', 'はい'], google=True), 0)
        self.assertEqual(self.run_dialog(['request', 'はい'], google=True, speech_error=True), 0)

    def test_stt_error_cancels_confirmation_and_listener_accepts_next_wake(self):
        self.assertEqual(self.run_dialog(['request', RuntimeError('local_transcription_failed'), 'はい', '자비스 취소'], google=True), 0)

    def test_separate_wake_and_end_routes_to_prompt_without_execution(self):
        self.assertEqual(self.run_dialog(['자비스', '시고토 오와리!', '아니요']), 0)

    def test_confirmed_followup_executes_once(self):
        self.assertEqual(self.run_dialog(['자비스 시고토 오와리', '예', '예']), 1)

    def test_observed_work_end_transcripts_require_new_confirmation(self):
        for transcript in ['시골토의 끝', '직업 끝']:
            with self.subTest(transcript=transcript):
                self.assertEqual(self.run_dialog(['자비스', transcript]), 0)
                self.assertEqual(self.run_dialog(['자비스', transcript, '예', '예']), 1)
                self.assertEqual(self.run_dialog(['자비스', transcript, '아니요', '예']), 0)

    def test_standalone_cancel_and_observed_transcription_never_call_model(self):
        self.assertEqual(self.run_dialog(['자비스 취소']), 0)
        self.assertEqual(self.run_dialog(['자비스 치솔 치솔']), 0)

    def test_cancel_and_failed_prompt_cannot_execute(self):
        self.assertEqual(self.run_dialog(['자비스 시고토 오와리', '아니요', '예']), 0)
        self.assertEqual(self.run_dialog(['자비스 시고토 오와리', '예'], speech_error=True), 0)
