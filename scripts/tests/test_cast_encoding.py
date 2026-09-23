from pathlib import Path
import sys, tempfile, subprocess, unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_mac_voice import CastOutput

class EncodingTests(unittest.TestCase):
    def test_aac_uses_selected_samples_and_private_audio_file(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'voice.wav';source.write_bytes(b'original')
            c=object.__new__(CastOutput)
            def encode(argv,**kwargs):
                self.assertIn('aac@44100',argv)
                self.assertEqual(argv[-2],str(source))
                Path(argv[-1]).write_bytes(b'AAC')
            with patch('jarvis_mac_voice.subprocess.run',side_effect=encode):
                target=c.prepare_media(source)
            self.assertEqual(target.suffix,'.m4a')
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
            self.assertEqual(source.read_bytes(),b'original')

    def test_encoder_failure_cleans_partial_file_and_redacts(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'voice.wav';source.write_bytes(b'original')
            target=source.with_suffix('.m4a');target.write_bytes(b'partial')
            c=object.__new__(CastOutput)
            with patch('jarvis_mac_voice.subprocess.run',side_effect=subprocess.CalledProcessError(1,['secret'])):
                with self.assertRaisesRegex(RuntimeError,'^speech_encoding_failed$'):c.prepare_media(source)
            self.assertFalse(target.exists())

class RetryTests(unittest.TestCase):
    def test_explicit_media_error_retries_audio_once_with_new_url(self):
        import threading
        from unittest.mock import Mock
        from jarvis_mac_voice import CastOutput
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'audio.wav';p.write_bytes(b'wave')
            c=object.__new__(CastOutput);c.cancelled=threading.Event();c.prepare_media=lambda path:path
            c._play_once=Mock(side_effect=[RuntimeError('cast_playback_error'),{'finished':True}])
            r=c.play(p,lambda _:None)
            self.assertEqual(r['audio_retries'],1)
            self.assertEqual(c._play_once.call_count,2)
            self.assertNotEqual(c._play_once.call_args_list[0].args[0],c._play_once.call_args_list[1].args[0])
            self.assertEqual(list(Path(d).iterdir()),[p])

    def test_interrupt_and_repeated_error_are_not_retried_indefinitely(self):
        import threading
        from unittest.mock import Mock
        from jarvis_mac_voice import CastOutput
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'audio.wav';p.write_bytes(b'wave')
            for error,count in [('cast_playback_interrupted',1),('existing_media_preserved',1),('cast_playback_timeout',1),('cast_playback_error',2)]:
                c=object.__new__(CastOutput);c.cancelled=threading.Event();c.prepare_media=lambda path:path
                c._play_once=Mock(side_effect=RuntimeError(error))
                with self.assertRaisesRegex(RuntimeError,error):c.play(p,lambda _:None)
                self.assertEqual(c._play_once.call_count,count)
