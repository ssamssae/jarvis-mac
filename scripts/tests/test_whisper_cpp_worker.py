import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import whisper_cpp_worker as worker

class WhisperWorkerTests(unittest.TestCase):
    def fixture(self, root):
        model=root/'model.bin';model.write_bytes(b'fake-model')
        wav=root/'input.wav'
        with wave.open(str(wav),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\x00\x00'*160)
        binary=root/'whisper-cli'
        binary.write_text('#!'+sys.executable+'\nimport sys,pathlib\na=sys.argv\nassert a[a.index("-l")+1]=="ko"\npathlib.Path(a[a.index("-of")+1]+".txt").write_text("자비스 테스트",encoding="utf-8")\n')
        binary.chmod(0o700)
        return model,wav,binary
    def test_actual_subprocess_json_protocol_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model,wav,binary=self.fixture(root)
            result=subprocess.run(worker.worker_command({'model':str(model),'whisper_cli':str(binary)}),
                input=json.dumps({'wav':str(wav)})+'\n',capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],
                             [{'ready':True},{'text':'자비스 테스트'}])
    def test_failure_is_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model,wav,binary=self.fixture(root)
            result=subprocess.run(worker.worker_command({'model':str(model),'whisper_cli':str(binary)}),
                input=json.dumps({'wav':str(root/'private-does-not-exist.wav')})+'\n',capture_output=True,text=True,timeout=5)
            self.assertNotIn('private-does-not-exist',result.stdout+result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[1]),{'error':'local_transcription_failed'})
    def test_pcm_format_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model,wav,binary=self.fixture(root)
            with wave.open(str(wav),'wb') as audio:
                audio.setnchannels(2);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\x00'*400)
            with self.assertRaisesRegex(ValueError,'16khz_mono'):worker.transcribe(binary,model,wav)
    def test_custom_worker_command(self):
        self.assertEqual(worker.worker_command({'stt_worker':'/example/worker','model':'/example/model'}),
                         ['/example/worker','/example/model'])
    def test_timeout_reaps_whisper(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model,wav,binary=self.fixture(root)
            binary.write_text('#!'+sys.executable+'\nimport time\ntime.sleep(20)\n')
            with self.assertRaises(subprocess.TimeoutExpired):worker.transcribe(binary,model,wav,timeout=.05)

if __name__=='__main__':unittest.main()
