"""Exercise button events through the real listener routing and speech queue."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis_mac_listener as app


class ButlerListenerTests(unittest.TestCase):
    def run_event(self, intent, *, configured=True, fail_speech=False):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            (root/'config.json').write_text(json.dumps({'cast_name':'fixture','model':'fixture','whisper_cli':'fixture'}))
            now = time.time()
            event = {'source':'butler-remote', 'intent':intent, 'speech_started_wall':now,
                     'speech_ended_wall':now, 'capture_ended_wall':now}
            stack.enter_context(patch.object(sys, 'argv', ['listener','--state-dir',str(root)]))
            stack.enter_context(patch.object(sys, 'stdin', io.StringIO(json.dumps(event)+'\n')))
            stack.enter_context(patch.object(sys, 'stdout', io.StringIO()))
            stack.enter_context(patch.object(app.signal, 'signal'))
            stack.enter_context(patch.object(app.time, 'sleep'))
            stt = stack.enter_context(patch.object(app, 'JSONWorker')).return_value
            cast = stack.enter_context(patch.object(app, 'CastSession')).return_value
            cast.startup_prepare_s = 0; cast.startup_prepare_error = None
            speech = stack.enter_context(patch.object(app, 'SpeechQueue')).return_value
            speech.events = []; speech.first_playing = None; speech.ack_first_playing = None
            if fail_speech:
                speech.submit.side_effect = RuntimeError('speech_synthesis_failed')
            home = stack.enter_context(patch.object(app, 'SmartHome')).return_value
            home.plan.return_value = {'matched':True} if intent.startswith('scene_') and configured else None
            home.execute.return_value = {'intent':'fixture', 'status':'ok', 'answer':'기존 씬 안내'}
            work = stack.enter_context(patch.object(app.jarvis_work_mode, 'execute', return_value={'status':'ok','answer':'기존 작전 안내'}))
            end = stack.enter_context(patch.object(app.jarvis_work_end, 'execute'))
            qa = stack.enter_context(patch.object(app, 'CursorQA'))
            app.main()
            receipt = json.loads((root/'last-turn.json').read_text())
            stt.send.assert_not_called(); qa.assert_not_called()
            return home, speech, work, end, receipt

    def test_scenes_use_same_home_execution_and_speech(self):
        for intent, phrase in [('scene_game','게임할거야'), ('scene_sleep','잘거야'), ('scene_away','외출')]:
            with self.subTest(intent=intent):
                home, speech, work, end, receipt = self.run_event(intent)
                home.plan.assert_called_once_with(phrase)
                home.execute.assert_called_once_with(home.plan.return_value, explicit_voice=True)
                speech.submit.assert_called_once_with('기존 씬 안내')
                work.assert_not_called(); end.assert_not_called()
                self.assertEqual(receipt['input_kind'], 'butler-remote')
                self.assertEqual(receipt['result'], 'pass')

    def test_work_start_runs_existing_work_and_speaks_answer(self):
        home, speech, work, end, receipt = self.run_event('work_start')
        work.assert_called_once(); end.assert_not_called(); home.execute.assert_not_called()
        speech.submit.assert_called_once_with('기존 작전 안내')
        self.assertEqual(receipt['result'], 'pass')

    def test_work_end_requires_existing_spoken_confirmation(self):
        home, speech, work, end, receipt = self.run_event('work_end')
        end.assert_not_called(); work.assert_not_called(); home.execute.assert_not_called()
        speech.submit.assert_called_once_with(app.jarvis_work_end.GOOGLE_PROMPT)
        self.assertEqual(receipt['work_end']['status'], 'prompt')

    def test_unconfigured_scene_does_not_fall_through_to_model(self):
        home, speech, _, _, receipt = self.run_event('scene_game', configured=False)
        home.execute.assert_not_called()
        self.assertEqual(receipt['pipeline']['route']['intent'], 'scene_unconfigured')

    def test_speech_failure_is_recorded_without_replaying_devices(self):
        home, _, _, _, receipt = self.run_event('scene_game', fail_speech=True)
        home.execute.assert_called_once()
        self.assertEqual(receipt['result'], 'error')
        self.assertEqual(receipt['error_code'], 'speech_synthesis_failed')
