"""Persistent Cast ownership, preparation recovery and per-turn audio lifecycle."""
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


class CastSessionTests(unittest.TestCase):
    def test_startup_connection_reused_until_shutdown(self):
        cast = MagicMock()
        with patch.object(app, 'CastOutput', return_value=cast) as factory:
            session = app.CastSession('speaker')
            directory = session.directory
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            try:
                session.prepare_startup()
                for _ in range(2):
                    receipt = {}
                    self.assertIs(session.connect(receipt), cast)
                    self.assertTrue(receipt['cast_reused'])
                    self.assertEqual(receipt['cast_connect_s'], 0)
                    self.assertEqual(receipt['startup_prepare_s'], session.startup_prepare_s)
                self.assertEqual(factory.call_count, 1)
                cast.close.assert_not_called()
            finally:
                session.close()
            cast.close.assert_called_once()
            self.assertFalse(directory.exists())

    def test_failed_startup_and_question_connect_recover_without_replay(self):
        cast = MagicMock()
        with patch.object(app, 'CastOutput', side_effect=[RuntimeError('offline'),
                                                        RuntimeError('existing_media_preserved'), cast]) as factory:
            session = app.CastSession('speaker')
            try:
                session.prepare_startup()  # Does not terminate the listener.
                self.assertIsNone(session.cast)
                self.assertEqual(session.startup_prepare_error, 'RuntimeError')
                receipt = {}
                with self.assertRaisesRegex(RuntimeError, 'existing_media_preserved'):
                    session.connect(receipt)
                self.assertFalse(receipt['cast_reused'])
                self.assertGreaterEqual(receipt['cast_connect_s'], 0)
                self.assertEqual(factory.call_count, 2)  # No same-turn retry.
                receipt = {}
                self.assertIs(session.connect(receipt), cast)
                self.assertFalse(receipt['cast_reused'])
                self.assertEqual(factory.call_count, 3)
            finally:
                session.close()

    def test_invalidation_reconnects_and_audio_cleanup_is_scoped(self):
        first, second = MagicMock(), MagicMock()
        with patch.object(app, 'CastOutput', side_effect=[first, second]):
            session = app.CastSession('speaker')
            try:
                session.prepare_startup()
                (session.directory/'turn.wav').write_bytes(b'audio')
                marker = session.directory/'marker'
                marker.write_text('not audio')
                session.clear_audio()
                self.assertFalse((session.directory/'turn.wav').exists())
                self.assertTrue(marker.exists())
                first.close.assert_not_called()
                session.invalidate()
                first.close.assert_called_once()
                self.assertIs(session.connect({}), second)
            finally:
                session.close()
            second.close.assert_called_once()


class ListenerCastIntegrationTests(unittest.TestCase):
    def check_turns(self, first_turn_fails=False, startup_fails=False, questions=2):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root/'audio'
            audio.mkdir()
            (root/'config.json').write_text(json.dumps({'cast_name': 'speaker', 'model': '/fixture/model',
                                                       'whisper_cli': 'whisper-cli'}))
            now = time.time()
            events = []
            for number in range(questions):
                path = audio/f'{number}.wav'
                path.write_bytes(b'x'*44)
                events.append({'wav': str(path), 'speech_started_wall': now-3,
                               'speech_ended_wall': now-1, 'capture_ended_wall': now})
            stt = MagicMock()
            stt.read.return_value = {'text': '자비스, 테스트 질문'}
            casts = [MagicMock(), MagicMock()]
            outputs, queues, turns = [], [], []

            def queue_factory(speech_root, cast):
                speech_root = Path(speech_root)
                self.assertNotEqual(speech_root, root)
                self.assertNotIn(root, speech_root.parents)
                self.assertEqual(list(speech_root.glob('*.wav')), [])
                outputs.append(speech_root)
                queue = MagicMock()
                queue.first_playing = None
                queues.append(queue)
                return queue

            def turn(*args, **kwargs):
                path = outputs[-1]/f'{len(turns)}.wav'
                path.write_bytes(b'speech')
                # Cleanup must happen after finish, not when generation returns.
                queues[-1].finish.side_effect = lambda: self.assertTrue(path.exists())
                turns.append(path)
                if first_turn_fails and len(turns) == 1:
                    raise RuntimeError('provider_failed')
                return {'generation_backend': 'cursor'}

            cast_results = ([RuntimeError('offline')] if startup_fails else []) + casts
            stdout = io.StringIO()
            with patch.object(sys, 'argv', ['listener', '--state-dir', str(root)]), \
                 patch.object(sys, 'stdin', io.StringIO(''.join(json.dumps(e)+'\n' for e in events))), \
                 patch.object(sys, 'stdout', stdout), patch.object(app.signal, 'signal'), \
                 patch.object(app.time, 'sleep'), patch.object(app, 'JSONWorker', return_value=stt), \
                 patch.object(app, 'CursorQA'), patch.object(app, 'CastOutput', side_effect=cast_results) as factory, \
                 patch.object(app, 'SpeechQueue', side_effect=queue_factory), \
                 patch.object(app, 'run_turn', side_effect=turn):
                app.main()
            expected_calls = 1 + int(startup_fails) + int(first_turn_fails)
            self.assertEqual(factory.call_count, expected_calls)
            self.assertEqual(len(turns), questions)
            for queue in queues:
                queue.finish.assert_called_once()
            for path in turns:
                self.assertFalse(path.exists())
            for speech_root in outputs:
                self.assertFalse(speech_root.exists())
            stt.close.assert_called_once()
            casts[0].close.assert_called_once()
            if first_turn_fails:
                casts[1].close.assert_called_once()
            states = [json.loads(line) for line in stdout.getvalue().splitlines()]
            initial_listening = next(state for state in states if state['state'] == 'listening')
            self.assertEqual(initial_listening['cast_prepared'], not startup_fails)
            receipt = json.loads((root/'last-turn.json').read_text())
            self.assertEqual(receipt['result'], 'pass')
            self.assertEqual(receipt['cast_reused'], not first_turn_fails)

    def test_two_turns_reuse_one_connection_and_clear_completed_audio(self):
        self.check_turns()

    def test_failed_turn_invalidates_then_next_question_reconnects(self):
        self.check_turns(first_turn_fails=True)

    def test_failed_startup_still_listens_and_next_question_prepares(self):
        self.check_turns(startup_fails=True)


if __name__ == '__main__':
    unittest.main()
