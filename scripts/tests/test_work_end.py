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
    def test_request_never_executes_and_bare_yes_never_executes(self):
        c = end.Confirmation()
        self.assertIsNone(c.accept('예', 1, 1))
        self.assertEqual(c.accept('일 끝', 2, 2), 'prompt')
        self.assertIsNone(c.accept('예', 3, 3))  # arm only after successful question playback
        for word in ['일 끝내지마', '일 끝이라고 말해', '일 끝 예']:
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
        self.assertIn('정상 종료를 요청', result['answer'])
        self.assertNotIn('종료했', result['answer'])
        run = Mock(return_value=subprocess.CompletedProcess([], 255, '', ''))
        self.assertEqual(end.execute(cfg, run)['status'], 'partial')
        self.assertEqual(end.execute({'steps': []}, run)['status'], 'unconfigured')


class RoutingTests(unittest.TestCase):
    def run_dialog(self, texts, speech_error=False):
        import jarvis_mac_listener as app
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'audio').mkdir()
            (root / 'config.json').write_text(json.dumps({'cast_name': 'fixture', 'model': 'fixture', 'work_end': {'steps': []}}))
            def events():
                for i in range(len(texts)):
                    clip = root / 'audio' / f'{i}.wav'; clip.write_bytes(b'x'*44)
                    now = time.time()
                    yield json.dumps({'wav': str(clip), 'speech_started_wall': now, 'speech_ended_wall': now, 'capture_ended_wall': now}) + '\n'
            stt = MagicMock(); stt.read.side_effect = [{'text': x} for x in texts]
            home = MagicMock(); home.plan.return_value = None
            speech = MagicMock(); speech.first_playing = None; speech.ack_first_playing = None; speech.events = []
            if speech_error: speech.finish.side_effect = RuntimeError('speaker_failed')
            indicator = MagicMock(); indicator.release.return_value = True
            with patch.object(sys, 'argv', ['listener', '--state-dir', str(root)]), patch.object(sys, 'stdin', events()), patch.object(sys, 'stdout', io.StringIO()), patch.object(app.signal, 'signal'), patch.object(app, 'SmartHome', return_value=home), patch.object(app, 'JSONWorker', return_value=stt), patch.object(app, 'CastOutput'), patch.object(app, 'SpeechQueue', return_value=speech), patch.object(app, 'StatusLight', return_value=indicator), patch.object(app, 'CursorQA') as qa, patch.object(app.jarvis_work_end, 'execute', return_value={'status': 'ok', 'answer': '종료를 요청했어요.'}) as execute:
                app.main()
            qa.assert_not_called(); home.execute.assert_not_called()
            return execute.call_count

    def test_confirmed_followup_executes_once(self):
        self.assertEqual(self.run_dialog(['자비스 일 끝', '예', '예']), 1)

    def test_standalone_cancel_and_observed_transcription_never_call_model(self):
        self.assertEqual(self.run_dialog(['자비스 취소']), 0)
        self.assertEqual(self.run_dialog(['자비스 치솔 치솔']), 0)

    def test_cancel_and_failed_prompt_cannot_execute(self):
        self.assertEqual(self.run_dialog(['자비스 일 끝', '아니요', '예']), 0)
        self.assertEqual(self.run_dialog(['자비스 일 끝', '예'], speech_error=True), 0)
