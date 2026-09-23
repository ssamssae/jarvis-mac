import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_mac_listener import WakeGate, checked_audio, atomic_json


class ListenerTests(unittest.TestCase):
    def test_wake_only_or_combined(self):
        gate = WakeGate()
        self.assertEqual(gate.accept('자비스, 하늘이 파란 이유', 1), ('question','하늘이 파란 이유'))
        self.assertEqual(gate.accept('헤이 자비스야 하늘이 파래?', 2), ('question','하늘이 파래?'))
        self.assertEqual(gate.accept('자비스하늘이 파란 이유', 3), ('question','하늘이 파란 이유'))
        self.assertEqual(gate.accept('Hey Jarvis, hello', 4), ('question','hello'))
    def test_english_pronunciation_aliases_and_boundaries(self):
        for name in ('Jarvis', 'JARVIS', '자르비스'):
            with self.subTest(name=name):
                gate = WakeGate()
                self.assertEqual(gate.accept(name, 1), ('armed', ''))
                self.assertEqual(gate.accept('복도 조명 꺼 주세요', 2),
                                 ('question', '복도 조명 꺼 주세요'))
                self.assertEqual(gate.accept('Hey ' + name + ', 복도 조명 꺼 주세요', 3),
                                 ('question', '복도 조명 꺼 주세요'))
        for text in ('Jarvice', 'Hey Jarvice', '잘바이스', '저비스', 'Jarvices test', 'Jarvison test', 'I said Jarvice', 'service', '저 비슷해'):
            self.assertEqual(WakeGate().accept(text, 1), ('ignored', ''))
        self.assertEqual(WakeGate().accept('헤이 자르비스', 1), ('armed', ''))

    def test_ambient_does_not_trigger(self):
        gate = WakeGate()
        for text in ['하늘이 파란 이유', '나는 자비스라고 말했다', 'Jarvison test', '안녕 자비스']:
            self.assertEqual(gate.accept(text, 1), ('ignored',''))
    def test_wake_window_single_use_and_expiry(self):
        gate = WakeGate(window=8)
        self.assertEqual(gate.accept('자비스', 1), ('armed',''))
        self.assertEqual(gate.accept('하늘이 파란 이유', 3)[0], 'question')
        self.assertEqual(gate.accept('또', 4)[0], 'ignored')
        gate.accept('자비스', 5)
        self.assertEqual(gate.accept('늦은 질문', 14)[0], 'ignored')
    def test_private_atomic_status_and_audio_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'test.wav'; path.write_bytes(b'x'*44)
            now = time.time()
            event = {'wav':str(path),'speech_started_wall':now-2,'speech_ended_wall':now-1,'capture_ended_wall':now}
            self.assertEqual(checked_audio(event, root), path)
            symlink = root/'link.wav'; symlink.symlink_to(path)
            with self.assertRaises(ValueError): checked_audio(dict(event, wav=str(symlink)), root)
            with self.assertRaises(ValueError): checked_audio(dict(event, capture_ended_wall=now-30), root)
            with self.assertRaises(ValueError): checked_audio(dict(event, wav=str(root.parent/'test.wav')), root)
            target = root/'status.json'; atomic_json(target, {'state':'listening'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(target.read_text())['state'], 'listening')


class ControllerTests(unittest.TestCase):
    def test_microphone_gates_cloud_deletes_raw_and_measures(self):
        self.check_controller(False)

    def test_generation_error_finishes_speech_before_cleanup(self):
        self.check_controller(True)

    def test_configured_voice_reaches_speech_queue(self):
        self.check_controller(False, voice="유나 (고품질)")

    def check_controller(self, failing, voice="Yuna"):
        import io
        from unittest.mock import patch, MagicMock
        import jarvis_mac_listener as app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); audio = root/'audio'; audio.mkdir()
            (root/'config.json').write_text(json.dumps({'cast_name':'Test speaker','model':'/example/model.bin','whisper_cli':'whisper-cli', 'voice':voice}))
            events = []
            now = time.time()
            for n in range(2):
                path = audio/f'{n}.wav'; path.write_bytes(b'x'*44)
                events.append({'wav':str(path),'speech_started_wall':now-3,'speech_ended_wall':now-1,'capture_ended_wall':now})
            stt = MagicMock(); stt.read.side_effect = [{'text':'일반 대화'}, {'text':'자비스, 하늘이 파란 이유'}]
            speech = MagicMock(); speech.first_playing = time.monotonic()
            speech.ack_first_playing = time.monotonic() - .5
            speech.events = []
            with patch.object(sys,'argv',['listener','--state-dir',str(root)]), \
                 patch.object(sys,'stdin',io.StringIO(''.join(json.dumps(e)+'\n' for e in events))), \
                 patch.object(sys,'stdout',io.StringIO()), \
                 patch.object(app.signal,'signal'), patch.object(app.time,'sleep'), \
                 patch.object(app,'JSONWorker',return_value=stt), \
                 patch.object(app,'CursorQA') as qa, patch.object(app,'CastOutput'), \
                 patch.object(app,'SpeechQueue',return_value=speech) as speech_factory, \
                 patch.object(app,'run_turn',return_value={'generation_backend':'cursor'}, side_effect=RuntimeError('test_failure') if failing else None) as turn:
                app.main()
            self.assertEqual(speech_factory.call_args.kwargs["voice"], voice)
            self.assertEqual(qa.call_count, 1)
            self.assertEqual(turn.call_args.args[0], '하늘이 파란 이유')
            self.assertFalse(turn.call_args.kwargs['reviewed_facts'])
            self.assertEqual(list(audio.iterdir()), [])
            receipt = json.loads((root/'last-turn.json').read_text())
            self.assertEqual(receipt['result'],'error' if failing else 'pass')
            self.assertGreater(receipt['speech_end_to_playing_s'],0)
            self.assertLess(receipt['speech_end_to_ack_s'], receipt['speech_end_to_playing_s'])
            speech.submit_acknowledgement.assert_called_once()
            self.assertNotIn('일반 대화',(root/'last-turn.json').read_text())
            speech.finish.assert_called_once()
            stt.close.assert_called_once()


class CancellationTests(unittest.TestCase):
    def test_abort_releases_inflight_playback(self):
        import threading
        from jarvis_mac_voice import SpeechQueue
        class Cast:
            def __init__(self):
                self.cancelled=threading.Event(); self.playing=threading.Event()
            def play(self, path, callback):
                self.playing.set()
                self.cancelled.wait(10)
                raise RuntimeError('speech_cancelled')
        class Queue(SpeechQueue):
            def synthesize(self, number, text): return Path('/unused'), 0
        cast=Cast()
        with tempfile.TemporaryDirectory() as directory:
            queue=Queue(directory, cast)
            queue.submit('test')
            self.assertTrue(cast.playing.wait(1))
            start=time.monotonic()
            queue.abort()
            with self.assertRaisesRegex(RuntimeError,'speech_cancelled'): queue.finish()
            self.assertLess(time.monotonic()-start,1)
            self.assertFalse(queue.thread.is_alive())

if __name__ == '__main__': unittest.main()

class WakeCueTests(unittest.TestCase):
    def test_wake_only_plays_before_arming_without_model_or_device(self):
        import io
        from unittest.mock import patch,MagicMock
        import jarvis_mac_listener as app
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);audio=root/'audio';audio.mkdir();clip=audio/'wake.wav';clip.write_bytes(b'x'*44)
            (root/'config.json').write_text(json.dumps({'cast_name':'fixture','model':'fixture','whisper_cli':'fixture'}))
            now=time.time();event={'wav':str(clip),'speech_started_wall':now-2,'speech_ended_wall':now-1,'capture_ended_wall':now}
            stt=MagicMock();stt.read.return_value={'text':'자비스'}
            cue=MagicMock();cue.events=[{'kind':'ack','finished':True}];cue.ack_first_playing=time.monotonic()
            output=io.StringIO()
            with patch.object(sys,'argv',['listener','--state-dir',str(root)]),patch.object(sys,'stdin',io.StringIO(json.dumps(event)+'\n')),patch.object(sys,'stdout',output),patch.object(app.signal,'signal'),patch.object(app,'JSONWorker',return_value=stt),patch.object(app,'CastOutput'),patch.object(app,'SpeechQueue',return_value=cue),patch.object(app,'SmartHome') as home,patch.object(app,'CursorQA') as qa:
                app.main()
            qa.assert_not_called();home.return_value.plan.assert_not_called();home.return_value.execute.assert_not_called()
            cue.submit_acknowledgement.assert_called_once();cue.finish.assert_called_once()
            states=[json.loads(x) for x in output.getvalue().splitlines()]
            self.assertLess(next(i for i,x in enumerate(states) if x['state']=='speaking'),next(i for i,x in enumerate(states) if x['state']=='armed'))
            self.assertEqual(next(x for x in states if x['state']=='armed')['armed_seconds'],8)
            self.assertEqual(json.loads((root/'last-wake.json').read_text())['result'],'pass')
            self.assertFalse((root/'last-turn.json').exists())
