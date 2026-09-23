#!/usr/bin/env python3
"""Local utterance wake gate for JarvisMacOSS.app. Only wake-qualified text leaves STT."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time

from jarvis_mac_voice import JSONWorker, CastOutput, SpeechQueue, run_turn
from jarvis_cursor_qa import CursorQA
from whisper_cpp_worker import worker_command

WAKE = re.compile(r"^\s*(?:(?:헤이|hey)\s*)?(?:자비스|jarvis)(?:야)?(?:[\s,.!?:，。！？]+|$)", re.I)
# Korean ASR commonly omits spaces between the vocative and the question.
KOREAN_WAKE = re.compile(r"^\s*(?:헤이\s*)?자비스(?:야)?[\s,.!?:，。！？]*")


class WakeGate:
    def __init__(self, window=8.0):
        self.window = window
        self.armed_until = 0.0

    def accept(self, text, now):
        match = WAKE.match(text) or KOREAN_WAKE.match(text)
        if match:
            question = text[match.end():].strip()
            self.armed_until = now + self.window if not question else 0.0
            return ('question', question) if question else ('armed', '')
        if now < self.armed_until:
            self.armed_until = 0.0
            return ('question', text.strip()) if text.strip() else ('ignored', '')
        self.armed_until = 0.0
        return 'ignored', ''


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def checked_audio(event, audio_dir):
    path = Path(event['wav'])
    if path.is_symlink() or path.parent.resolve() != audio_dir.resolve() or path.suffix != '.wav':
        raise ValueError('invalid_audio_path')
    if not path.is_file() or not 44 <= path.stat().st_size <= 4000000:
        raise ValueError('invalid_audio_file')
    for key in ('speech_started_wall', 'speech_ended_wall', 'capture_ended_wall'):
        if not isinstance(event.get(key), (float, int)):
            raise ValueError('invalid_capture_timing')
    if not event['speech_started_wall'] <= event['speech_ended_wall'] <= event['capture_ended_wall']:
        raise ValueError('invalid_capture_order')
    if not 0 <= time.time() - event['capture_ended_wall'] < 20:
        raise ValueError('stale_capture')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.state_dir; root.mkdir(parents=True, exist_ok=True, mode=0o700)
    audio_dir = root/'audio'; audio_dir.mkdir(exist_ok=True, mode=0o700)
    lock = (root/'listener.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('listener_already_running')
    config = json.loads((root/'config.json').read_text())
    gate = WakeGate()
    def state(name, listen, **extra):
        payload = {'state':name, 'listen':listen, 'updated_at':time.time(), 'pid':os.getpid(), **extra}
        atomic_json(root/'status.json', payload)
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    active_speech = None
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
        if active_speech: active_speech.abort()
        raise KeyboardInterrupt
    def finish_speech(speech):
        try: speech.finish()
        except Exception:
            if not stopping: raise
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    stt = None
    try:
        state('preparing', False)
        stt = JSONWorker(worker_command(config))
        state('listening', True)
        for line in sys.stdin:
            event = json.loads(line)
            path = checked_audio(event, audio_dir)
            state('recognizing', False)
            began = time.monotonic()
            try:
                stt.send({'wav':str(path)})
                text = stt.read()['text'].strip()
            finally:
                path.unlink(missing_ok=True)
            stt_s = time.monotonic() - began
            kind, question = gate.accept(text, time.monotonic())
            if kind != 'question':
                # Ambient speech and recognition text are never persisted or sent to QA.
                state('armed' if kind == 'armed' else 'listening', True,
                      armed_seconds=gate.window if kind == 'armed' else 0)
                continue
            state('answering', False)
            receipt = {'input_kind':'microphone', 'wake_mode':'local_transcription_utterance_prefix',
                       'recognized_text':text, 'question':question, 'stt_s':stt_s,
                       'speech_started_wall':event['speech_started_wall'],
                       'speech_ended_wall':event['speech_ended_wall'],
                       'capture_ended_wall':event['capture_ended_wall'],
                       'endpoint_s':event['capture_ended_wall']-event['speech_ended_wall']}
            try:
                with tempfile.TemporaryDirectory(prefix='speech-', dir=root) as directory, contextlib.ExitStack() as cleanup:
                    qa = CursorQA(config.get('cursor_binary', str(Path.home()/'.local/bin/agent')))
                    cleanup.callback(qa.close)
                    started = time.monotonic()
                    cast = CastOutput(config['cast_name'], directory)
                    cleanup.callback(cast.close)
                    receipt['cast_connect_s'] = time.monotonic()-started
                    speech = SpeechQueue(directory, cast)
                    active_speech = speech
                    cleanup.callback(finish_speech, speech)
                    # The listener only answers; device commands remain disabled.
                    pipeline = run_turn(question, qa, speech, reviewed_facts=False)
                    receipt['pipeline'] = pipeline
                    if speech.first_playing is not None:
                        playing_wall = time.time()-(time.monotonic()-speech.first_playing)
                        receipt['playing_wall'] = playing_wall
                        receipt['speech_end_to_playing_s'] = playing_wall-event['speech_ended_wall']
                        receipt['speech_start_to_playing_s'] = playing_wall-event['speech_started_wall']
                    receipt['result'] = 'pass'
            except Exception as exc:
                receipt['result'] = 'error'
                # Avoid raw provider output, credentials, or unrelated source paths.
                receipt['error_type'] = type(exc).__name__
                receipt['error_code'] = str(exc) if str(exc) in {
                    'cursor_keychain_locked', 'cursor_login_required', 'cursor_timeout',
                    'existing_media_preserved', 'exact_cast_target_missing'} else 'turn_failed'
            active_speech = None
            receipt['finished_wall'] = time.time()
            atomic_json(root/'last-turn.json', receipt)
            time.sleep(.5)  # Discard Nest tail before rearming, not a new capture queue.
            state('listening', True, last_result=receipt['result'])
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        if stt: stt.close()
        for path in audio_dir.glob('*.wav'):
            path.unlink(missing_ok=True)
        with contextlib.suppress(BrokenPipeError):
            state('stopped', False)
        lock.close()


if __name__ == '__main__':
    main()
