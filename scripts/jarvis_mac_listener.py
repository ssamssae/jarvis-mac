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
from jarvis_smart_home import SmartHome
from jarvis_weather import weather_reply
from jarvis_status_light import StatusLight
import jarvis_work_mode
import jarvis_work_end
from jarvis_control_inbox import events as control_events
from whisper_cpp_worker import worker_command

WAKE = re.compile(r"^\s*(?:(?:헤이|hey)\s*)?(?:자비스|자르비스|jarvis)(?:야)?(?:[\s,.!?:，。！？]+|$)", re.I)
# Korean ASR commonly omits spaces between the vocative and the question.
KOREAN_WAKE = re.compile(r"^\s*(?:헤이\s*)?(?:자비스|자르비스)(?:야)?[\s,.!?:，。！？]*")
HEY_PREFIX = re.compile(r"^\s*(?:헤이|hey\b)", re.I)
ENGLISH_WAKE_ONLY = re.compile(r"\s*hey[\s,.!?:]+jarvis[\s,.!?:]*", re.I)


def transcribe_for_gate(stt, path, gate, *, english_retry=True, speech_seconds=None, japanese_confirmation=False):
    # Freeze the follow-up state before STT so a slow decode cannot change its language.
    waiting_for_question = time.monotonic() < gate.armed_until
    stt.send({'wav': str(path), **({'language': 'ja'} if japanese_confirmation else {})})
    text = stt.read()['text'].strip()
    if japanese_confirmation:
        return text
    # A sub-400ms burst cannot plausibly contain a long sentence. Keep short
    # replies/cancel controls; do not consume the follow-up window for noise.
    if (speech_seconds is not None and speech_seconds < .4
            and sum(c.isalnum() for c in text) > 8):
        return ''
    short_wake = speech_seconds is not None and .4 <= speech_seconds <= 2.5
    if (english_retry and not waiting_for_question
            and (short_wake or HEY_PREFIX.match(text))
            and not (WAKE.match(text) or KOREAN_WAKE.match(text))):
        stt.send({'wav': str(path), 'language': 'en'})
        english = stt.read()['text'].strip()
        # Never replace a Korean question with an English transcript or execute its tail.
        if ENGLISH_WAKE_ONLY.fullmatch(english):
            return 'Hey Jarvis'
    return text


def require_indicator_release(indicator, speech, receipt):
    # Do not run a device routine while status-light restoration is uncertain.
    # Explain the blocked request before normal queue cleanup; never replay it.
    receipt['stage'] = 'status_light_release'
    if not indicator.release():
        answer = '상태등을 정리하지 못해서 요청을 실행하지 않았어요.'
        receipt['pipeline'].update(route={'intent': 'blocked'}, answer=answer)
        speech.submit(answer)
        raise RuntimeError('status_light_restore_failed')
    receipt['stage'] = 'routine'


def is_cancel(text):
    # Exact standalone controls only; never rewrite substrings in ordinary questions.
    word = re.sub(r"[\s,.!?:，。！？]+", "", text).lower()
    return word in {'취소', '취소해', '취소해줘', '취소해주세요', '그만', '그만해',
                    '그만해줘', 'cancel', '치즈소', '치치소', '치솔'} or bool(
                        re.fullmatch(r'(?:취소){2,3}', word))


class WakeGate:
    def __init__(self, window=8.0):
        self.window = window
        self.armed_until = 0.0

    def accept(self, text, now):
        match = WAKE.match(text) or KOREAN_WAKE.match(text)
        question = text[match.end():].strip() if match else text.strip()
        if (match or now < self.armed_until) and is_cancel(question):
            self.armed_until = 0.0
            return 'cancelled', ''
        if match:
            question = text[match.end():].strip()
            self.armed_until = now + self.window if not question else 0.0
            return ('question', question) if question else ('armed', '')
        if now < self.armed_until:
            if not text.strip():
                return 'ignored', ''
            self.armed_until = 0.0
            return 'question', text.strip()
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


class CastSession:
    """One listener-owned connection and a private, audio-only HTTP document root."""
    def __init__(self, name):
        self.name = name
        # Never serve the state/config directory, even during recovery.
        self._temporary = tempfile.TemporaryDirectory(prefix='jarvis-listener-speech-')
        self.directory = Path(self._temporary.name)
        self.cast = None
        self.startup_prepare_s = 0.0
        self.startup_prepare_error = None

    def prepare_startup(self):
        metrics = {}
        try:
            self.connect(metrics)
        except Exception as exc:
            # Keep microphone/wake recognition available if the speaker is offline/busy.
            self.startup_prepare_error = type(exc).__name__
        finally:
            self.startup_prepare_s = metrics['cast_connect_s']

    def connect(self, receipt):
        receipt['startup_prepare_s'] = self.startup_prepare_s
        receipt['startup_prepare_error'] = self.startup_prepare_error
        receipt['cast_reused'] = self.cast is not None
        receipt['cast_connect_s'] = 0.0
        if self.cast is None:
            started = time.monotonic()
            try:
                self.cast = CastOutput(self.name, str(self.directory))
            finally:
                receipt['cast_connect_s'] = time.monotonic() - started
        return self.cast

    def clear_audio(self):
        # Called only after the current speech worker finishes (or is cancelled).
        for path in self.directory.glob('*.wav'):
            path.unlink(missing_ok=True)

    def invalidate(self):
        cast, self.cast = self.cast, None
        if cast is not None:
            cast.close()

    def close(self):
        try:
            self.invalidate()
        finally:
            self._temporary.cleanup()


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
    work_end = jarvis_work_end.Confirmation()
    home = SmartHome(config.get("smart_home"))
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
    qa = None
    cast_session = None
    indicator = StatusLight(config.get("status_light"), root)
    try:
        state('preparing', False)
        stt = JSONWorker(worker_command(config))
        cast_session = CastSession(config['cast_name'])
        cast_session.prepare_startup()
        state('listening', True, cast_prepared=cast_session.cast is not None,
              startup_prepare_s=cast_session.startup_prepare_s,
              startup_prepare_error=cast_session.startup_prepare_error)
        for event in control_events(sys.stdin, root):
            external_end = event.get('source') == 'google-home-matter' and event.get('intent') == 'work_end'
            if external_end:
                if time.monotonic() < work_end.until:
                    continue  # A repeated remote request never extends a confirmation.
                text, kind, question, stt_s = '', 'question', '일 끝', 0
                gate.armed_until = 0
            else:
                path = checked_audio(event, audio_dir)
                state('recognizing', False)
                began = time.monotonic()
                try:
                    text = transcribe_for_gate(stt, path, gate,
                                               english_retry=not config.get('stt_worker'),
                                               speech_seconds=event['speech_ended_wall'] - event['speech_started_wall'],
                                               japanese_confirmation=work_end.japanese and time.monotonic() < work_end.until)
                finally:
                    path.unlink(missing_ok=True)
                stt_s = time.monotonic() - began
                kind, question = gate.accept(text, time.monotonic())
            if WAKE.match(text) or KOREAN_WAKE.match(text):
                indicator.show('blue')
            if kind != 'question':
                # Ambient speech and recognition text are never persisted or sent to QA.
                if kind in {'armed', 'cancelled'}:
                    state('speaking', False)
                    if kind == 'cancelled':
                        work_end.cancel()
                    wake_receipt = {'input_kind':'cancel' if kind == 'cancelled' else 'wake_only', 'stt_s':stt_s,
                                    'capture_ended_wall':event['capture_ended_wall']}
                    cue = None
                    try:
                        cast = cast_session.connect(wake_receipt)
                        cue = SpeechQueue(cast_session.directory, cast, voice=config.get('voice', 'Yuna'))
                        active_speech = cue
                        if kind == 'cancelled':
                            cue.submit('취소했습니다.')
                        else:
                            cue.submit_acknowledgement()
                        cue.finish()
                        wake_receipt.update(result='pass', speech=cue.events)
                        if cue.ack_first_playing is not None:
                            played = time.time() - (time.monotonic() - cue.ack_first_playing)
                            wake_receipt['capture_end_to_cue_s'] = played - event['capture_ended_wall']
                    except Exception as exc:
                        cast_session.invalidate()
                        wake_receipt.update(result='error', error_type=type(exc).__name__,
                                            error_code=str(exc) if str(exc) in {
                                                'cast_receiver_status_unavailable', 'cast_media_status_unavailable',
                                                'cast_playback_timeout', 'cast_playback_error',
                                                'existing_media_preserved'} else 'cue_failed')
                    finally:
                        if cue is not None and stopping: cue.abort()
                        active_speech = None
                        cast_session.clear_audio()
                    wake_receipt['finished_wall'] = time.time()
                    atomic_json(root/'last-wake.json', wake_receipt)
                    # Cue time must not consume the user's follow-up window.
                    if kind == 'armed':
                        gate.armed_until = time.monotonic() + gate.window
                        indicator.show('green', ttl=gate.window)
                    else:
                        indicator.release()
                remaining = gate.window if kind == 'armed' else max(0.0, gate.armed_until - time.monotonic())
                state('armed' if remaining else 'listening', True,
                      armed_seconds=remaining)
                continue
            indicator.show('yellow')
            state('answering', False)
            receipt = {'input_kind':'google-home-matter' if external_end else 'microphone', 'wake_mode':'local_transcription_utterance_prefix',
                       'recognized_text':text, 'question':question, 'stt_s':stt_s,
                       'configured_voice':config.get('voice', 'Yuna'),
                       'speech_started_wall':event['speech_started_wall'],
                       'speech_ended_wall':event['speech_ended_wall'],
                       'capture_ended_wall':event['capture_ended_wall'],
                       'endpoint_s':event['capture_ended_wall']-event['speech_ended_wall']}
            speech = None
            receipt['pipeline'] = {}
            try:
                with contextlib.ExitStack() as cleanup:
                    cast = cast_session.connect(receipt)
                    speech = SpeechQueue(cast_session.directory, cast, voice=config.get('voice', 'Yuna'))
                    active_speech = speech
                    cleanup.callback(finish_speech, speech)
                    # Acknowledge only a recognized, wake-qualified question. The
                    # same queue serializes this sound before the eventual answer,
                    # while Cursor generation proceeds on this controller thread.
                    speech.submit_acknowledgement()
                    plan = home.plan(question)
                    ending = work_end.accept(question, time.monotonic(), event['speech_started_wall'])
                    work = jarvis_work_mode.matches(question)
                    weather = weather_reply(question, config.get('weather')) if plan is None and not work and ending is None else None
                    if ending is not None:
                        require_indicator_release(indicator, speech, receipt)
                        if ending == 'execute':
                            result = jarvis_work_end.execute(config.get('work_end'))
                        else:
                            prompt = jarvis_work_end.GOOGLE_PROMPT if external_end else jarvis_work_end.PROMPT
                            result = {'status': ending, 'answer': prompt if ending == 'prompt' else jarvis_work_end.CANCELLED}
                        receipt['work_end'] = result
                        pipeline = receipt['pipeline']
                        pipeline.update(route={'intent':'work_end'}, answer=result['answer'])
                        speech.submit(result['answer'])
                        speech.finish()
                        if ending == 'prompt':
                            work_end.arm(time.monotonic(), time.time(), japanese=external_end)
                    elif work:
                        require_indicator_release(indicator, speech, receipt)
                        result = jarvis_work_mode.execute(config.get('work_mode'))
                        receipt['work_mode'] = result
                        pipeline = receipt['pipeline']
                        pipeline.update(route={'intent':'work_mode'}, answer=result['answer'])
                        speech.submit(result['answer'])
                        speech.finish()
                    elif weather is not None:
                        receipt['weather'] = weather
                        pipeline = receipt['pipeline']
                        pipeline.update(route={'intent':'weather'}, answer=weather['answer'], sources=weather['sources'])
                        speech.submit(weather['answer'])
                        speech.finish()
                    elif plan is not None:
                        # Release before explicit appliance commands so restoration cannot undo them.
                        require_indicator_release(indicator, speech, receipt)
                        result = home.execute(plan, explicit_voice=True)
                        receipt['smart_home'] = result
                        pipeline = receipt['pipeline']
                        pipeline.update(route={'intent': result['intent']}, answer=result['answer'])
                        speech.submit(result['answer'])
                        speech.finish()
                    else:
                        if qa is None:
                            qa = CursorQA(config.get('cursor_binary', str(Path.home()/'.local/bin/agent')),
                                          persistent=True)
                        pipeline = run_turn(question, qa, speech, reviewed_facts=False,
                                            metrics=receipt['pipeline'], conversation=True, stream=True)
                    receipt['pipeline'] = pipeline
                    if speech.first_playing is not None:
                        playing_wall = time.time()-(time.monotonic()-speech.first_playing)
                        receipt['playing_wall'] = playing_wall
                        receipt['speech_end_to_playing_s'] = playing_wall-event['speech_ended_wall']
                        receipt['speech_start_to_playing_s'] = playing_wall-event['speech_started_wall']
                    receipt['result'] = 'pass'
            except Exception as exc:
                # No replay/retry within a turn: reconnect only on the next question.
                cast_session.invalidate()
                work_end.cancel()
                receipt['result'] = 'error'
                # Avoid raw provider output, credentials, or unrelated source paths.
                receipt['error_type'] = type(exc).__name__
                receipt['error_code'] = str(exc) if str(exc) in {
                    'cursor_keychain_locked', 'cursor_login_required', 'cursor_timeout', 'cursor_model_unavailable',
                    'existing_media_preserved', 'exact_cast_target_missing',
                    'cast_status_unavailable', 'cast_playback_timeout',
                    'cast_receiver_status_unavailable', 'cast_media_status_unavailable',
                    'cast_playback_error', 'cast_playback_interrupted', 'cast_playback_cancelled',
                    'speech_synthesis_failed', 'speech_encoding_failed', 'speech_queue_timeout',
                    'status_light_restore_failed'} else 'turn_failed'
            finally:
                if speech is not None:
                    receipt['pipeline']['speech'] = speech.events
                    if speech.ack_first_playing is not None:
                        ack_wall = time.time()-(time.monotonic()-speech.ack_first_playing)
                        receipt['ack_playing_wall'] = ack_wall
                        receipt['speech_end_to_ack_s'] = ack_wall-event['speech_ended_wall']
                    if speech.first_playing is not None:
                        playing_wall = time.time()-(time.monotonic()-speech.first_playing)
                        receipt['playing_wall'] = playing_wall
                        receipt['speech_end_to_playing_s'] = playing_wall-event['speech_ended_wall']
                        receipt['speech_start_to_playing_s'] = playing_wall-event['speech_started_wall']
                indicator.release()
                cast_session.clear_audio()
                active_speech = None
            receipt['finished_wall'] = time.time()
            atomic_json(root/'last-turn.json', receipt)
            time.sleep(.5)  # Discard Nest tail before rearming, not a new capture queue.
            remaining = max(0, work_end.until - time.monotonic())
            if remaining:
                gate.armed_until = work_end.until
                indicator.show('green', ttl=remaining)
            state('armed' if remaining else 'listening', True, armed_seconds=remaining, last_result=receipt['result'])
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        indicator.close()
        if qa: qa.close()
        if cast_session: cast_session.close()
        if stt: stt.close()
        for path in audio_dir.glob('*.wav'):
            path.unlink(missing_ok=True)
        with contextlib.suppress(BrokenPipeError):
            state('stopped', False)
        lock.close()


if __name__ == '__main__':
    main()
