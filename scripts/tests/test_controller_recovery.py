"""Controller rejection and diagnostics, with all real side effects mocked."""
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


class RecoveryTests(unittest.TestCase):
    def run_controller(self, root, events, stt):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['listener', '--state-dir', str(root)]), \
             patch.object(sys, 'stdout', output), patch.object(app.signal, 'signal'), \
             patch.object(app, 'control_events', return_value=iter(events)), \
             patch.object(app, 'JSONWorker', return_value=stt), \
             patch.object(app, 'CastSession') as cast, \
             patch.object(app, 'StatusLight') as light, \
             patch.object(app, 'SmartHome') as home, \
             patch.object(app, 'CursorQA') as qa, \
             patch.object(app, 'SpeechQueue') as speech:
            cast.return_value.startup_prepare_s = 0
            cast.return_value.startup_prepare_error = None
            app.main()
            home.return_value.execute.assert_not_called()
            qa.assert_not_called()
            speech.assert_not_called()
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_bad_capture_does_not_kill_listener_or_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); audio = root/'audio'; audio.mkdir()
            (root/'config.json').write_text(json.dumps({'cast_name':'fixture', 'model':'fixture', 'whisper_cli':'fixture'}))
            good = audio/'good.wav'; good.write_bytes(b'x'*44)
            stale = audio/'stale.wav'; stale.write_bytes(b'x'*44)
            outside = root/'secret.wav'; outside.write_bytes(b'x'*44)
            now = time.time()
            event = {'wav':str(good), 'speech_started_wall':now-2, 'speech_ended_wall':now-1, 'capture_ended_wall':now}
            events = [{}, dict(event, wav=None), dict(event, wav=str(audio/'missing.wav')),
                      dict(event, wav=str(outside)), dict(event, wav=str(stale),
                           speech_started_wall=now-42, speech_ended_wall=now-41, capture_ended_wall=now-40), event]
            stt = MagicMock(); stt.read.return_value = {'text':'일반 대화'}
            states = self.run_controller(root, events, stt)
            self.assertEqual(sum(x.get('last_result') == 'capture_rejected' for x in states), 5)
            self.assertTrue(any(x['state'] == 'listening' for x in states))
            self.assertEqual(states[-1]['state'], 'stopped')
            self.assertEqual(stt.send.call_count, 2) # Only the final good clip; Korean + wake retry.
            self.assertTrue(outside.exists())
            self.assertFalse(stale.exists())
            error = json.loads((root/'last-controller-error.json').read_text())
            self.assertEqual(error['code'], 'stale_capture')
            self.assertEqual(error['stage'], 'capture')

    def test_unexpected_failure_is_recorded_without_sensitive_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.json').write_text(json.dumps({'cast_name':'fixture', 'model':'fixture', 'whisper_cli':'fixture'}))
            def broken_events():
                raise RuntimeError('SECRET transcript and credential')
                yield
            with self.assertRaises(RuntimeError):
                self.run_controller(root, broken_events(), MagicMock())
            path = root/'last-controller-error.json'
            self.assertNotIn('SECRET', path.read_text())
            self.assertEqual(json.loads(path.read_text())['error_type'], 'RuntimeError')
            self.assertEqual(json.loads(path.read_text())['stage'], 'controller')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
