"""Input acknowledgement is serialized audio, not answer-generation completion."""
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis_mac_voice as voice


class FixtureQueue(voice.SpeechQueue):
    def synthesize(self, number, text):
        path = self.directory / f'{self.prefix}-sentence-{number}.wav'
        path.write_bytes(b'fixture')
        return path, 0


class AcknowledgementTests(unittest.TestCase):
    def test_wave_is_private_mono_pcm_with_quiet_tones_and_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = FixtureQueue(directory)
            queue.submit_acknowledgement()
            queue.finish()
            path = next(Path(directory).glob('*-ack.wav'))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with wave.open(str(path), 'rb') as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 24000))
                self.assertAlmostEqual(audio.getnframes() / 24000, .7)
                data = audio.readframes(audio.getnframes())
            samples = struct.unpack('<' + 'h' * (len(data)//2), data)
            self.assertLessEqual(max(map(abs, samples)), 3000)
            self.assertGreater(max(map(abs, samples)), 1000)
            self.assertTrue(all(value == 0 for value in samples[:720]))
            self.assertTrue(all(value == 0 for value in samples[8000:]))
            self.assertEqual(queue.events[0]['kind'], 'ack')
            self.assertNotIn('text', queue.events[0])
            self.assertIsNone(queue.first_playing)
            self.assertIsNone(queue.ack_first_playing)  # No device: not a playback receipt.
        self.assertFalse(path.exists())

    def test_generation_runs_during_ack_and_answer_follows_without_overlap(self):
        ack_started, release_ack, generation_started = threading.Event(), threading.Event(), threading.Event()
        order = []
        class Cast:
            cancelled = threading.Event()
            def play(self, path, callback):
                if path.name.endswith('-ack.wav'):
                    order.append('ack_start'); callback(time.monotonic()); ack_started.set()
                    if not release_ack.wait(3): raise RuntimeError('fixture_timeout')
                    order.append('ack_end')
                else:
                    order.append('answer_start'); callback(time.monotonic())
                return {'finished': True}
        class QA:
            def ask(self, *_args, **_kwargs):
                generation_started.set()
                yield {'answer': '테스트 답변입니다.', 'done': True, 'backend': 'cursor'}
        with tempfile.TemporaryDirectory() as directory:
            queue = FixtureQueue(directory, Cast())
            queue.submit_acknowledgement()
            result, errors = {}, []
            def generate():
                try:
                    result.update(voice.run_turn('질문', QA(), queue, reviewed_facts=False))
                except BaseException as exc:
                    errors.append(exc)
            with patch.object(voice, 'retrieve', return_value=[{'url': 'fixture', 'text': 'evidence'}]):
                worker = threading.Thread(target=generate)
                worker.start()
                try:
                    self.assertTrue(ack_started.wait(1))
                    self.assertTrue(generation_started.wait(1))
                    self.assertIsNotNone(queue.ack_first_playing)
                    self.assertIsNone(queue.first_playing)
                    self.assertNotIn('answer_start', order)
                finally:
                    release_ack.set()
                    worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(order, ['ack_start', 'ack_end', 'answer_start'])
            self.assertLess(queue.ack_first_playing, queue.first_playing)
            self.assertEqual([event['kind'] for event in result['speech']], ['ack', 'answer'])
            self.assertGreaterEqual(result['first_playing_s'], 0)

    def test_failed_ack_never_plays_answer_or_reports_answer_start(self):
        played = []
        class Cast:
            cancelled = threading.Event()
            def play(self, path, callback):
                played.append(path.name)
                callback(time.monotonic())
                raise RuntimeError('cast_playback_timeout')
        with tempfile.TemporaryDirectory() as directory:
            queue = FixtureQueue(directory, Cast())
            queue.submit_acknowledgement()
            queue.submit('답변')
            with self.assertRaisesRegex(RuntimeError, 'cast_playback_timeout'):
                queue.finish()
            self.assertEqual(len(played), 1)
            self.assertTrue(played[0].endswith('-ack.wav'))
            self.assertIsNotNone(queue.ack_first_playing)
            self.assertIsNone(queue.first_playing)
            self.assertEqual(queue.events, [])  # No fabricated FINISHED event.

    def test_cancelling_ack_stops_queue_without_answer_or_retry(self):
        started = threading.Event()
        class Cast:
            def __init__(self): self.cancelled = threading.Event(); self.calls = 0
            def play(self, path, callback):
                self.calls += 1
                callback(time.monotonic()); started.set()
                self.cancelled.wait(3)
                raise RuntimeError('speech_cancelled')
        with tempfile.TemporaryDirectory() as directory:
            cast = Cast()
            queue = FixtureQueue(directory, cast)
            queue.submit_acknowledgement(); queue.submit('답변')
            self.assertTrue(started.wait(1))
            queue.abort()
            with self.assertRaisesRegex(RuntimeError, 'speech_cancelled'):
                queue.finish()
            self.assertFalse(queue.thread.is_alive())
            self.assertEqual(cast.calls, 1)
            self.assertIsNone(queue.first_playing)

    def test_ack_is_once_and_first_and_regular_answer_does_not_add_it(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = FixtureQueue(directory)
            queue.submit_acknowledgement()
            with self.assertRaisesRegex(RuntimeError, 'first_and_once'):
                queue.submit_acknowledgement()
            queue.finish()
            regular = FixtureQueue(directory)
            regular.submit('일반 답변')
            with self.assertRaisesRegex(RuntimeError, 'first_and_once'):
                regular.submit_acknowledgement()
            regular.finish()
            self.assertEqual([event['kind'] for event in regular.events], ['answer'])
            self.assertFalse(regular.ack_requested)


if __name__ == '__main__': unittest.main()
