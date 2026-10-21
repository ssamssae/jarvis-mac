"""Owner-only local prompt requests, multiplexed with native microphone events."""
import io
import json
import os
from pathlib import Path
import select
import socket
import time

from jarvis_dictation import START
from jarvis_shuttle import SOURCE as SHUTTLE_SOURCE, validate_request as shuttle_request


def dictation_target(request):
    """A preset "node engine" phrase for dictation_start, or None when absent/invalid."""
    target = request.get('target')
    if target is None:
        return None
    if not isinstance(target, str) or not 0 < len(target) <= 40 or not START.fullmatch(target.lower()):
        raise ValueError('invalid_target')
    return target


def microphone_ready(root):
    """False only when the native app reports its microphone engine stopped.

    JarvisMacOSS writes native-status.json next to the socket; after 마이크 재연결 실패
    or a manual pause, engine_running is false and no speech can ever be captured.
    A missing or damaged report belongs to the native app and does not refuse dictation.
    """
    try:
        status = json.loads((Path(root) / 'native-status.json').read_text())
    except (OSError, ValueError):
        return True
    return not (isinstance(status, dict) and status.get('engine_running') is False)


# Fixed button intents share the listener's normal dispatch and speech path.
BUTLER_PHRASES = {'scene_game': '게임할거야', 'scene_sleep': '잘거야',
                  'scene_away': '외출', 'work_start': '시고토스타토',
                  'work_end': '시고토 오와리'}


def _native_event(line):
    """A damaged native event must not disconnect subsequent microphone events."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def events(stream, root):
    # In-memory streams are used by controller tests, never by the native app.
    try:
        fd = stream.fileno()
    except (AttributeError, io.UnsupportedOperation):
        for line in stream:
            event = _native_event(line)
            if event is not None:
                yield event
        return
    path = Path(root) / 'work-end.sock'
    path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    buffer = b''
    last_request = {}
    try:
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(8)
        while True:
            ready, _, _ = select.select([fd, server], [], [])
            # Already captured speech is always processed before a new prompt.
            if fd in ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    return
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    event = _native_event(line)
                    if event is not None:
                        yield event
            if server in ready:
                accepted = False
                shuttle_event = None
                client, _ = server.accept()
                with client:
                    client.settimeout(.5)
                    try:
                        with client.makefile('rb') as reader:
                            message = reader.readline(2048)
                        request = json.loads(message)
                        created = request.get('created_at')
                        intent = request.get('intent')
                        source = request.get('source', 'google-home-matter')
                        if source == SHUTTLE_SOURCE:
                            # Another local script's sound or helper (jarvis_shuttle). Those may
                            # follow each other within seconds, so no duplicate window applies.
                            try:
                                shuttle_event = shuttle_request(request, root)
                            except ValueError:
                                shuttle_event = None
                            accepted = shuttle_event is not None
                            client.sendall(b'queued\n' if accepted else b'rejected\n')
                        else:
                            try:
                                target = dictation_target(request) if intent == 'dictation_start' else None
                                target_ok = True
                            except ValueError:
                                target, target_ok = None, False
                            allowed = (isinstance(intent, str) and intent in BUTLER_PHRASES if source == 'butler-remote'
                                       else source == 'google-home-matter' and intent in ('work_end', 'dictation_start'))
                            accepted = (allowed and target_ok
                                        and isinstance(created, (float, int))
                                        and 0 <= time.time() - created < 3
                                        and time.monotonic() - last_request.get(intent, -float('inf')) >= 15)
                            reply = b'queued\n' if accepted else b'rejected\n'
                            if accepted and intent == 'dictation_start' and not microphone_ready(root):
                                # Arming dictation without a microphone says 말씀하세요 and holds the
                                # status light for a capture that never comes (2026-10-18 hallway light).
                                # Refused presses must not consume the duplicate window either.
                                accepted, reply = False, b'microphone_unavailable\n'
                            client.sendall(reply)
                    except (OSError, ValueError, AttributeError):
                        accepted = False
                if accepted and shuttle_event is not None:
                    yield shuttle_event
                elif accepted:
                    last_request[intent] = time.monotonic()
                    now = time.time()
                    event = {'source': source, 'intent': intent,
                             'speech_started_wall': now, 'speech_ended_wall': now,
                             'capture_ended_wall': now}
                    if target:
                        event['target'] = target
                    yield event
    finally:
        server.close()
        path.unlink(missing_ok=True)
