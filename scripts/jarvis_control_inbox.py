"""Owner-only local prompt requests, multiplexed with native microphone events."""
import io
import json
import os
from pathlib import Path
import select
import socket
import time


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
        server.listen(2)
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
                client, _ = server.accept()
                with client:
                    client.settimeout(.5)
                    try:
                        with client.makefile('rb') as reader:
                            message = reader.readline(256)
                        request = json.loads(message)
                        created = request.get('created_at')
                        intent = request.get('intent')
                        accepted = (intent in ('work_end', 'dictation_start')
                                    and isinstance(created, (float, int))
                                    and 0 <= time.time() - created < 3
                                    and time.monotonic() - last_request.get(intent, -float('inf')) >= 15)
                        client.sendall(b'queued\n' if accepted else b'rejected\n')
                    except (OSError, ValueError, AttributeError):
                        accepted = False
                if accepted:
                    last_request[intent] = time.monotonic()
                    now = time.time()
                    yield {'source': 'google-home-matter', 'intent': intent,
                           'speech_started_wall': now, 'speech_ended_wall': now,
                           'capture_ended_wall': now}
    finally:
        server.close()
        path.unlink(missing_ok=True)
