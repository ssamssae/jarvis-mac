#!/usr/bin/env python3
"""Private voice ingress using existing bridge transports, never shell speech."""
import argparse
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import time
import select
import subprocess
import sys
from types import SimpleNamespace
import uuid


def load_bridge(root, engine):
    name = {'codex':'codex-repl-telegram-bridge.py', 'cursor':'cursor-telegram-bridge.py',
            'grok':'grok-telegram-bridge.py'}[engine]
    # Imports expose existing transports only. No Telegram polling/sending is started.
    os.environ['CUB_DRY_RUN'] = '1'
    os.environ['GRB_DRY_RUN'] = '1'
    if engine == 'grok':
        fifos = list((Path.home()/'.claude/state').glob('grok-bridge-*.fifo'))
        if len(fifos) == 1:
            os.environ['GRB_NAME'] = fifos[0].name[len('grok-bridge-'):-len('.fifo')]
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('voice_bridge_' + engine, root/name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def transcript_mark(path):
    path = Path(path)
    return path, path.read_text()


def user_text_count(data, text):
    def contains_user(value):
        if isinstance(value, dict):
            if value.get('role') == 'user' or value.get('type') in {'user', 'user_message'}:
                def strings(obj):
                    if isinstance(obj, str): return [obj]
                    if isinstance(obj, dict): return sum((strings(v) for v in obj.values()), [])
                    if isinstance(obj, list): return sum((strings(v) for v in obj), [])
                    return []
                if any(text in part for part in strings(value)): return True
            return any(contains_user(v) for v in value.values())
        if isinstance(value, list): return any(contains_user(v) for v in value)
        return False
    count = 0
    for line in data.splitlines():
        try: count += bool(contains_user(json.loads(line)))
        except ValueError: pass
    return count


def user_text_seen(mark, text, source=None):
    path, baseline = mark
    baseline = baseline if isinstance(baseline, str) else ''
    before = user_text_count(baseline, text)
    started = time.time()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if source:
            candidate = source()
            if candidate and Path(candidate) != path and Path(candidate).stat().st_mtime >= started-2:
                path, before = Path(candidate), 0
        if user_text_count(path.read_text(), text) > before:
            return 'submitted'
        time.sleep(.2)
    return 'unverified'


def codex_composer_empty(screen):
    rows = [line for line in screen.splitlines() if re.sub(r'\x1b\[[0-9;]*m', '', line).lstrip().startswith('›')]
    if not rows:
        return False
    row = rows[-1].split('›', 1)[1]
    dim = False
    for part in re.split(r'(\x1b\[[0-9;]*m)', row):
        if part.startswith('\x1b['):
            for code in part[2:-1].split(';'):
                if code in {'', '0', '22'}: dim = False
                elif code == '2': dim = True
        elif part.strip() and not dim:
            return False
    return True


def local_submit(root, request, probe=False):
    engine = request['engine']
    bridge = load_bridge(root, engine)
    text = request['text']
    if engine == 'codex':
        config = SimpleNamespace(tmux_bin='tmux', tmux_socket='codex', session_target='=codex',
                                 pane_target='=codex:0.0', submit_key='Enter', enter_count=1)
        transport = bridge.TmuxTransport(config)
        with transport.composer_lock():
            screen = transport.capture_visible_screen()
            if (bridge.parse_approval_prompt(screen) or bridge.parse_choice_prompt(screen)):
                return 'busy'
            ansi = transport.tmux('capture-pane', '-e', '-p', '-t', config.pane_target).stdout
            if not codex_composer_empty(ansi):
                return 'composer_occupied'
            if not probe:
                path = bridge.session_file_from_descendants(transport.pane_pid())
                if not path: return 'unverified'
                mark = transcript_mark(path)
                transport._paste_prompt_unlocked(text)
        return 'ready' if probe else user_text_seen(mark, text, lambda: bridge.session_file_from_descendants(transport.pane_pid()))
    if engine == 'cursor':
        with bridge.composer_lock():
            screen = bridge._tui_capture_pane()
            if (bridge._tui_pane_shows_approval_overlay(screen) or bridge._tui_pane_shows_choice_menu(screen)
                    or bridge._tui_pane_shows_model_picker(screen)):
                return 'busy'
            if not bridge._tui_compose_shows_idle_placeholder(screen):
                return 'composer_occupied'
            if not probe:
                mark = transcript_mark(bridge._pick_active_transcript_path())
                bridge._tui_paste_unlocked(text)
        return 'ready' if probe else user_text_seen(mark, text, lambda: bridge._pick_active_transcript_path(str(mark[0]), text))
    # The existing FIFO serializes with Grok's own job lock.
    if bridge.is_awaiting_human():
        if request.get('origin') != 'microphone' or probe:
            return 'waiting_human'
    if not bridge._tui_repl_idle_probe() or not bridge.model_composer_empty():
        return 'busy'
    state = Path.home()/'.claude/state'
    candidates = [p for p in state.glob('grok-bridge-*.fifo') if stat.S_ISFIFO(p.stat().st_mode)]
    if len(candidates) != 1:
        return 'unconfigured'
    if probe:
        return 'ready'
    text = text.replace('\n', ' ')
    mark = transcript_mark(bridge.tui_history_path())
    payload = (text + '\n').encode()
    if bridge.is_awaiting_human() and request.get('origin') == 'microphone':
        bridge.clear_awaiting_human()
    fd = os.open(candidates[0], os.O_WRONLY | os.O_NONBLOCK)
    try:
        deadline = time.monotonic() + 5
        while payload:
            if time.monotonic() > deadline: return 'unknown'
            if select.select([], [fd], [], .2)[1]:
                payload = payload[os.write(fd, payload):]
    finally:
        os.close(fd)
    return user_text_seen(mark, text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--bridge-root', type=Path, default=Path.home()/'claude-automations/scripts')
    args = parser.parse_args()
    os.umask(0o077)
    os.environ['PATH'] = '/opt/homebrew/bin:/usr/local/bin:' + os.environ.get('PATH', '')
    request = json.loads(sys.stdin.read(100000))
    uuid.UUID(request['id'])
    if request['engine'] not in {'codex','grok','cursor'} or request['node'] not in {'macbook14','mac','macmini'}:
        raise ValueError('unknown_target')
    if not isinstance(request['text'], str) or not request['text'].strip() or len(request['text']) > 64000:
        raise ValueError('invalid_text')
    if args.local:
        store = Path.home()/'.local/state/jarvis-session-input'
        store.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt = store/(request['id'] + '.json')
        with (store/'receiver.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if receipt.exists() and not args.probe:
                print(receipt.read_text())
                return
            result = {'id':request['id'], 'status':'unknown'}
            if not args.probe:
                receipt.write_text(json.dumps(result))  # claim before irreversible input
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result['status'] = local_submit(args.bridge_root, request, args.probe)
            except Exception:
                result['status'] = 'unknown'
            if not args.probe:
                receipt.write_text(json.dumps(result))
    else:
        config = json.loads(args.config.read_text())
        argv = config['nodes'][request['node']]
        if args.probe:
            argv = [*argv, '--probe']
        process = subprocess.run(argv, input=json.dumps(request), text=True, capture_output=True, timeout=40)
        result = json.loads(process.stdout) if process.returncode == 0 else {'id':request['id'],'status':'unknown'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
