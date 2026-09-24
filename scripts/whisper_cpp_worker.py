#!/usr/bin/env python3
"""Local JSON-lines STT adapter. Starts whisper-cli for each utterance, not warm inference."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import wave


def worker_command(config):
    model = str(config.get('model') or '')
    custom = config.get('stt_worker')
    if custom:
        return [str(Path(custom).expanduser()), model]
    return [sys.executable, str(Path(__file__).resolve()), '--model', model,
            '--whisper-cli', str(config.get('whisper_cli') or 'whisper-cli')]


def transcribe(binary, model, wav, language='ko', timeout=50):
    path = Path(wav).expanduser().resolve(strict=True)
    if not path.is_file() or not 44 <= path.stat().st_size <= 4_000_000:
        raise ValueError('invalid_wav')
    with wave.open(str(path), 'rb') as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
            raise ValueError('expected_16khz_mono_pcm16')
        frames = audio.getnframes()
        short_pcm = audio.readframes(frames) if frames < 32000 else None
    with tempfile.TemporaryDirectory(prefix='jarvis-stt-') as directory:
        output = Path(directory)/'transcript'
        if short_pcm is not None:
            # whisper-cli can skip sub-second audio. Preserve speech bytes and add
            # silence only; never force a transcript with a wake-word prompt.
            path = Path(directory)/'padded.wav'
            leading = b'\x00' * (5600 * 2)
            trailing = b'\x00' * (16000 * 2)
            with wave.open(str(path), 'wb') as padded:
                padded.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                padded.writeframes(leading + short_pcm + trailing)
        process = subprocess.Popen([str(binary), '-m', str(model), '-f', str(path),
            '-l', language, '-nt', '-otxt', '-of', str(output)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            if process.wait(timeout=timeout) != 0: raise RuntimeError('whisper_failed')
            result = output.with_suffix('.txt')
            if not result.is_file() or result.stat().st_size > 32000:
                raise RuntimeError('whisper_missing_or_large_output')
            return result.read_text(encoding='utf-8').strip()
        finally:
            # The worker owns this process group; no other transcriber is touched.
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--whisper-cli', default='whisper-cli')
    parser.add_argument('--language', default='ko')
    args = parser.parse_args()
    os.umask(0o077)
    binary = shutil.which(args.whisper_cli)
    model = args.model.expanduser().resolve()
    if not binary or not model.is_file():
        print(json.dumps({'error':'stt_binary_or_model_missing'}), flush=True)
        return 1
    def stop(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        print(json.dumps({'ready': True}), flush=True)
        for line in sys.stdin:
            try:
                if len(line) > 16384: raise ValueError('request_too_large')
                request = json.loads(line)
                language = request.get('language', args.language)
                if 'language' in request and language not in ('ko', 'en'):
                    raise ValueError('invalid_request_language')
                result = transcribe(binary, model, request['wav'], language)
                print(json.dumps({'text':result}, ensure_ascii=False), flush=True)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.TimeoutExpired, wave.Error):
                print(json.dumps({'error':'local_transcription_failed'}), flush=True)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
