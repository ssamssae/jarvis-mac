"""Reuse the installed Jarvis work-start routine; no shell or remote command input."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import time


def execute(state_dir):
    state_dir = Path(state_dir).resolve()
    spec = importlib.util.spec_from_file_location('jarvis_existing_work_start', state_dir / 'runtime/jarvis_work_mode.py')
    routine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routine)
    config = json.loads((state_dir / 'config.json').read_text())
    return routine.execute(config.get('work_mode'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--receipt-dir', type=Path, required=True)
    args = parser.parse_args()
    args.receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.receipt_dir / 'start.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        result = execute(args.state_dir)
        receipt = {'source': 'google-home-matter', 'intent': 'work_mode', 'at': time.time(), **result}
        temporary = args.receipt_dir / 'last-start.tmp'
        temporary.write_text(json.dumps(receipt, ensure_ascii=False))
        temporary.chmod(0o600)
        os.replace(temporary, args.receipt_dir / 'last-start.json')
        print(json.dumps({'status': result['status']}))


if __name__ == '__main__':
    main()
