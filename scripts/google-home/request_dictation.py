"""Start local guided dictation; no transcript or target is accepted from Matter."""
import argparse
import json
from pathlib import Path
import socket
import time


def request(state_dir, target=None):
    """The listener's answer: 'queued', 'microphone_unavailable' (engine stopped) or 'rejected'."""
    payload = {'intent': 'dictation_start', 'created_at': time.time()}
    if target:
        # A preset "node engine" phrase (for example "아테나 클로드") skips the spoken questions.
        payload['target'] = target
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(Path(state_dir) / 'work-end.sock'))
        client.sendall((json.dumps(payload, ensure_ascii=False) + '\n').encode())
        reply = client.recv(64).rstrip(b'\n')
    return reply.decode() if reply in (b'queued', b'microphone_unavailable') else 'rejected'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--target', help='preset "node engine" phrase, e.g. "아테나 클로드"')
    args = parser.parse_args()
    try:
        status = request(args.state_dir, args.target)
    except OSError:
        status = 'unavailable'  # The listener socket is not there at all.
    print(json.dumps({'status': status}))
    raise SystemExit(0 if status == 'queued' else 1)
