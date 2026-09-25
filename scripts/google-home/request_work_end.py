"""Request a spoken confirmation; this adapter cannot execute work-end."""
import argparse
import json
from pathlib import Path
import socket
import time


def request(state_dir):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(Path(state_dir) / 'work-end.sock'))
        client.sendall((json.dumps({'intent': 'work_end', 'created_at': time.time()}) + '\n').encode())
        return client.recv(64) == b'queued\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        accepted = request(args.state_dir)
    except OSError:
        accepted = False
    print(json.dumps({'status': 'queued' if accepted else 'unavailable'}))
    raise SystemExit(0 if accepted else 1)
