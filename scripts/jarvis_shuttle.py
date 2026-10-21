#!/usr/bin/env python3
"""Local shuttle: play audio or run a configured helper through the listener's own process.

macOS local-network permission follows the responsible app. Scripts started by launchd or
tmux cannot reach the Nest or a LAN lamp on their own, while the listener running under
JarvisMacOSS.app can. A caller copies a WAV to `<state>/shuttle/<id>.wav`, sends
`{"source": "nest-shuttle", "intent": "cast_wav", ...}` to the listener's `work-end.sock`
and waits for `<state>/shuttle/<id>.json`. `helper` runs only an argv listed under
`config.json` `shuttle.helpers`; a request names a helper and passes a small JSON payload
on its stdin, never a command.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

SOURCE = 'nest-shuttle'
INTENTS = ('cast_wav', 'helper')
REQUEST_ID = re.compile(r'[0-9a-f]{32}')
HELPER_NAME = re.compile(r'[a-z][a-z0-9_]{0,31}')
ERROR_CODE = re.compile(r'[a-z][a-z0-9_]{2,47}')
STATE = Path.home() / 'Library/Application Support/JarvisMacOSS'
MAX_AUDIO_BYTES = 16_000_000
MAX_PAYLOAD_BYTES = 1024
MAX_WAIT_S = 300.0      # A request may wait behind a running Jarvis turn, never longer.
CLOCK_SKEW_S = 5.0
HELPER_TIMEOUT_S = 30.0
LEFTOVER_S = 600.0      # Files of callers that gave up are removed after this age.


class ShuttleError(RuntimeError):
    """Transport failure: shuttle_unavailable, shuttle_rejected or shuttle_timeout."""


def inbox(state_dir):
    return Path(state_dir) / 'shuttle'


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_request(request, root, now=None):
    """Return the listener event for an acceptable request, or raise ValueError(code)."""
    now = time.time() if now is None else now
    if not isinstance(request, dict) or request.get('source') != SOURCE:
        raise ValueError('invalid_source')
    intent = request.get('intent')
    if not isinstance(intent, str) or intent not in INTENTS:
        raise ValueError('unknown_intent')
    key = request.get('id')
    if not isinstance(key, str) or not REQUEST_ID.fullmatch(key):
        raise ValueError('invalid_id')
    created, expires = request.get('created_at'), request.get('expires')
    if not (_number(created) and _number(expires)) or not created < expires <= created + MAX_WAIT_S:
        raise ValueError('invalid_expiry')
    if created > now + CLOCK_SKEW_S or now >= expires:
        raise ValueError('expired')
    event = {'source': SOURCE, 'intent': intent, 'id': key, 'expires': float(expires)}
    if intent == 'cast_wav':
        path = inbox(root) / (key + '.wav')
        if path.is_symlink() or not path.is_file():
            raise ValueError('missing_audio')
        if not 44 <= path.stat().st_size <= MAX_AUDIO_BYTES:
            raise ValueError('invalid_audio_file')
        event['wav'] = str(path)
        return event
    name = request.get('name')
    if not isinstance(name, str) or not HELPER_NAME.fullmatch(name):
        raise ValueError('invalid_helper')
    payload = request.get('payload', {})
    if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError('invalid_payload')
    event['name'] = name
    event['payload'] = payload
    return event


# ---------------------------------------------------------------- listener side

def _code(exc, default='shuttle_failed'):
    text = str(exc)
    return text if ERROR_CODE.fullmatch(text) else default


def _cast(event, cast_session):
    source = Path(event['wav'])
    # The Cast HTTP server serves only the session's private directory.
    target = Path(cast_session.directory) / ('shuttle-' + event['id'] + '.wav')
    try:
        shutil.move(str(source), str(target))
    except FileNotFoundError:
        raise RuntimeError('missing_audio') from None
    try:
        try:
            cast = cast_session.connect({})
            played = cast.play(target, lambda at: None)
        except Exception:
            # Same rule as Jarvis' own turns: reconnect on the next use, never replay now.
            cast_session.invalidate()
            raise
    finally:
        target.unlink(missing_ok=True)
    return {'finished': bool(played.get('finished')), 'cast_start_s': played.get('cast_start_s')}


def _helper(event, config):
    helpers = ((config or {}).get('shuttle') or {}).get('helpers') or {}
    argv = helpers.get(event['name']) if isinstance(helpers, dict) else None
    if not (isinstance(argv, list) and argv and all(isinstance(part, str) and part for part in argv)):
        raise RuntimeError('helper_not_configured')
    timeout = max(0.1, min(HELPER_TIMEOUT_S, event['expires'] - time.time()))
    try:
        done = subprocess.run(argv, input=json.dumps(event['payload'], ensure_ascii=False), text=True,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError('helper_timeout') from None
    except OSError:
        raise RuntimeError('helper_unavailable') from None
    # Helper stderr is never stored: device libraries can include secrets.
    if done.returncode:
        raise RuntimeError('helper_failed')
    lines = done.stdout.strip().splitlines()
    try:
        output = json.loads(lines[-1]) if lines else None
    except ValueError:
        output = None
    return {'output': output}


def _write(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(path)


def sweep(box, now=None, age=LEFTOVER_S):
    now = time.time() if now is None else now
    try:
        entries = list(Path(box).iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if (path.is_symlink() or path.is_file()) and now - path.lstat().st_mtime > age:
                path.unlink()
        except OSError:
            pass


def serve(event, root, config, cast_session):
    """Run one accepted request inside the listener and leave its result file.

    Never raises an ordinary exception: another script's sound must not stop Jarvis.
    """
    box = inbox(root)
    result = {'id': event.get('id'), 'intent': event.get('intent')}
    try:
        if time.time() >= event['expires']:
            raise RuntimeError('shuttle_expired')
        if event['intent'] == 'cast_wav':
            result.update(_cast(event, cast_session))
        else:
            result.update(_helper(event, config))
        result['result'] = 'pass'
    except Exception as exc:
        result['result'] = 'error'
        result['error_code'] = _code(exc)
    finally:
        if event.get('wav'):
            Path(event['wav']).unlink(missing_ok=True)
    try:
        _write(box / (event['id'] + '.json'), result)
    except (OSError, KeyError, TypeError):
        pass
    sweep(box)
    return result


# ------------------------------------------------------------------ caller side

def _send(state_dir, message, timeout):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        try:
            client.connect(str(Path(state_dir) / 'work-end.sock'))
        except OSError as exc:
            raise ShuttleError('shuttle_unavailable') from exc
        try:
            client.sendall((json.dumps(message, ensure_ascii=False) + '\n').encode())
            reply = client.recv(64).rstrip(b'\n')
        except TimeoutError as exc:
            raise ShuttleError('shuttle_timeout') from exc
        except OSError as exc:
            raise ShuttleError('shuttle_unavailable') from exc
    if reply != b'queued':
        raise ShuttleError('shuttle_rejected')


def _wait(path, deadline, poll=0.05):
    while True:
        try:
            text = path.read_text()
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise ShuttleError('shuttle_timeout') from None
            time.sleep(poll)
            continue
        path.unlink(missing_ok=True)
        result = json.loads(text)
        if result.get('result') != 'pass':
            raise RuntimeError(result.get('error_code') or 'shuttle_failed')
        return result


def _request(state_dir, message, timeout, audio=None):
    deadline = time.monotonic() + timeout
    now = time.time()
    message.update(source=SOURCE, created_at=now, expires=now + timeout)
    try:
        _send(state_dir, message, timeout)
        return _wait(inbox(state_dir) / (message['id'] + '.json'), deadline)
    except ShuttleError:
        if audio is not None:
            audio.unlink(missing_ok=True)  # Expiry already stops a late play.
        raise


def _box(state_dir):
    box = inbox(state_dir)
    box.mkdir(mode=0o700, exist_ok=True)
    return box


def cast_wav(path, *, state_dir=STATE, timeout=60.0):
    """Play one WAV on the listener's Nest; returns its receipt (`finished`, `cast_start_s`)."""
    if not 0 < timeout <= MAX_WAIT_S:
        raise ValueError('timeout must be within (0, 300] seconds')
    key = uuid.uuid4().hex
    audio = _box(state_dir) / (key + '.wav')
    shutil.copyfile(path, audio)
    os.chmod(audio, 0o600)
    return _request(state_dir, {'intent': 'cast_wav', 'id': key}, timeout, audio)


def run_helper(name, payload, *, state_dir=STATE, timeout=HELPER_TIMEOUT_S):
    """Run a helper named in the listener configuration with `payload` JSON on its stdin."""
    if not 0 < timeout <= MAX_WAIT_S:
        raise ValueError('timeout must be within (0, 300] seconds')
    _box(state_dir)
    return _request(state_dir, {'intent': 'helper', 'id': uuid.uuid4().hex, 'name': name,
                                'payload': payload}, timeout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE)
    parser.add_argument('--timeout', type=float, default=60.0)
    commands = parser.add_subparsers(dest='command', required=True)
    cast = commands.add_parser('cast', help='play one WAV file on the configured Nest')
    cast.add_argument('wav', type=Path)
    helper = commands.add_parser('helper', help='run a configured helper; JSON payload on stdin')
    helper.add_argument('name')
    args = parser.parse_args(argv)
    try:
        if args.command == 'cast':
            result = cast_wav(args.wav, state_dir=args.state_dir, timeout=args.timeout)
        else:
            payload = json.loads(sys.stdin.read() or '{}')
            result = run_helper(args.name, payload, state_dir=args.state_dir, timeout=args.timeout)
    except (RuntimeError, ValueError, OSError) as exc:
        print(json.dumps({'result': 'error', 'error_code': _code(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
