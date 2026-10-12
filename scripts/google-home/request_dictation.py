"""Start local guided dictation; no transcript or target is accepted from Matter."""
import argparse
import json
from pathlib import Path
import socket
import time


def request(state_dir, target=None):
    payload = {'intent': 'dictation_start', 'created_at': time.time()}
    if target:
        # A preset "node engine" phrase (for example "아테나 클로드") skips the spoken questions.
        payload['target'] = target
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(Path(state_dir) / 'work-end.sock'))
        client.sendall((json.dumps(payload, ensure_ascii=False) + '\n').encode())
        return client.recv(64) == b'queued\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--target', help='preset "node engine" phrase, e.g. "아테나 클로드"')
    args = parser.parse_args()
    try:
        accepted = request(args.state_dir, args.target)
    except OSError:
        accepted = False
    print(json.dumps({'status': 'queued' if accepted else 'unavailable'}))
    raise SystemExit(0 if accepted else 1)
