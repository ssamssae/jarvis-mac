"""One private Cursor ACP process, fresh bounded-context sessions, no tool grants."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time


class CursorACP:
    def __init__(self, binary, config_directory, timeout=90):
        self.binary = binary
        self.config_directory = Path(config_directory)
        self.timeout = timeout
        self.process = None
        self.directory = None
        self.pending = bytearray()
        self.stderr = bytearray()
        self.sequence = 0
        self.sessions = 0

    def _send(self, message, deadline):
        data = memoryview((json.dumps(message, ensure_ascii=False) + '\n').encode())
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('cursor_timeout')
            if not select.select([], [self.process.stdin], [], remaining)[1]:
                raise TimeoutError('cursor_timeout')
            try: data = data[os.write(self.process.stdin.fileno(), data):]
            except BlockingIOError: pass
            except BrokenPipeError: raise RuntimeError('cursor_acp_disconnected') from None

    def _read(self, deadline):
        while b'\n' not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('cursor_timeout')
            ready = select.select([self.process.stdout, self.process.stderr], [], [], remaining)[0]
            if not ready: raise TimeoutError('cursor_timeout')
            for pipe in ready:
                block = os.read(pipe.fileno(), 65536)
                if not block and pipe is self.process.stdout:
                    raise RuntimeError('cursor_acp_disconnected')
                target = self.pending if pipe is self.process.stdout else self.stderr
                target.extend(block)
                if len(target) > 1000000: raise RuntimeError('cursor_output_too_large')
        line, _, rest = self.pending.partition(b'\n'); self.pending[:] = rest
        try: message = json.loads(line)
        except (ValueError, UnicodeError): raise RuntimeError('cursor_invalid_json') from None
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise RuntimeError('cursor_invalid_json')
        return message

    def _reject_request(self, message, deadline):
        if 'id' not in message or 'method' not in message: return False
        method = message['method']
        if method == 'session/request_permission':
            options = message.get('params', {}).get('options', [])
            rejected = next((o.get('optionId') for o in options if o.get('kind') == 'reject_once'), None)
            outcome = {'outcome': 'selected', 'optionId': rejected} if rejected else {'outcome': 'cancelled'}
            reply = {'result': {'outcome': outcome}}
        elif method == 'cursor/ask_question':
            reply = {'result': {'outcome': 'skipped'}}
        elif method == 'cursor/create_plan':
            reply = {'result': {'outcome': 'rejected'}}
        else:
            reply = {'error': {'code': -32601, 'message': 'Client capability unavailable'}}
        self._send({'jsonrpc': '2.0', 'id': message['id'], **reply}, deadline)
        return True

    def _request(self, method, params, deadline):
        self.sequence += 1
        self._send({'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params}, deadline)
        return self.sequence

    @staticmethod
    def _result(message):
        if 'error' in message:
            error = json.dumps(message['error']).lower()
            if 'unknown model' in error or 'model not found' in error:
                raise RuntimeError('cursor_model_unavailable')
            if 'auth' in error or 'login' in error:
                raise RuntimeError('cursor_login_required')
            raise RuntimeError('cursor_acp_request_failed')
        result = message.get('result')
        if not isinstance(result, dict): raise RuntimeError('cursor_invalid_json')
        return result

    def _call(self, method, params, deadline):
        request_id = self._request(method, params, deadline)
        while True:
            message = self._read(deadline)
            if self._reject_request(message, deadline): continue
            if message.get('id') == request_id: return self._result(message)

    def _start(self, deadline):
        if self.process is not None and self.process.poll() is None: return False
        self.close()
        source = self.config_directory / 'cli-config.json'
        if not source.is_file(): raise RuntimeError('cursor_configuration_missing')
        config = json.loads(source.read_text())
        if not isinstance(config, dict): raise RuntimeError('cursor_configuration_invalid')
        if not config.get('model') and not config.get('selectedModel'):
            raise RuntimeError('cursor_selected_model_missing')
        config['permissions'] = {'allow': [], 'deny': ['Shell(*)', 'Read(**)', 'Read(/**)',
            'Write(**)', 'Write(/**)', 'WebFetch(*)', 'WebSearch(*)', 'Mcp(*:*)']}
        config['approvalMode'] = 'allowlist'
        self.directory = tempfile.TemporaryDirectory(prefix='jarvis-cursor-acp-')
        root = Path(self.directory.name)
        self.workspace = root / 'workspace'; self.workspace.mkdir(mode=0o700)
        isolated = root / 'configuration'; isolated.mkdir(mode=0o700)
        target = isolated / 'cli-config.json'; target.write_text(json.dumps(config)); target.chmod(0o600)
        env = os.environ.copy(); env['CURSOR_CONFIG_DIR'] = str(isolated)
        for name in ('CURSOR_API_KEY', 'CURSOR_AUTH_TOKEN'): env.pop(name, None)
        self.process = subprocess.Popen([str(self.binary), '--mode', 'ask', 'acp'],
            cwd=self.workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            os.set_blocking(pipe.fileno(), False)
        self._call('initialize', {'protocolVersion': 1, 'clientCapabilities': {
            'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False},
            'clientInfo': {'name': 'jarvis-voice', 'version': '1'}}, deadline)
        self._call('authenticate', {'methodId': 'cursor_login'}, deadline)
        return True

    def ask(self, prompt, stream=False):
        if not isinstance(prompt, str) or not prompt or len(prompt) > 18000:
            raise ValueError('invalid_cursor_prompt')
        start = time.monotonic(); deadline = start + self.timeout; completed = False
        try:
            # Bound Cursor's private session files as well as our model context.
            if self.sessions >= 32: self.close()
            started = self._start(deadline)
            session = self._call('session/new', {'cwd': str(self.workspace), 'mcpServers': []}, deadline)
            session_id = session.get('sessionId')
            if not isinstance(session_id, str) or not session_id: raise RuntimeError('cursor_invalid_session')
            self.sessions += 1
            self._call('session/set_mode', {'sessionId': session_id, 'modeId': 'ask'}, deadline)
            timings = {'init_s': time.monotonic() - start, 'first_delta_s': None, 'result_s': None,
                       'process_reused': not started}
            request_id = self._request('session/prompt', {'sessionId': session_id,
                'prompt': [{'type': 'text', 'text': prompt}]}, deadline)
            answer = ''; received = 0
            while True:
                message = self._read(deadline); received += 1
                if received > 20000: raise RuntimeError('cursor_output_too_large')
                if self._reject_request(message, deadline): continue
                if message.get('id') == request_id:
                    result = self._result(message)
                    if result.get('stopReason') != 'end_turn' or not answer.strip():
                        raise RuntimeError('cursor_no_success_result')
                    timings['result_s'] = time.monotonic() - start
                    completed = True
                    yield {'done': True, 'answer': answer.strip(), 'backend': 'cursor',
                           'elapsed_s': timings['result_s'], 'timings': timings}
                    return
                if message.get('method') != 'session/update': continue
                params = message.get('params', {})
                if params.get('sessionId') != session_id: continue
                update = params.get('update', {})
                if update.get('sessionUpdate') == 'current_mode_update' and update.get('currentModeId') != 'ask':
                    raise RuntimeError('cursor_acp_mode_changed')
                if update.get('sessionUpdate') != 'agent_message_chunk': continue
                content = update.get('content', {})
                if content.get('type') != 'text': continue
                chunk = content.get('text')
                if not isinstance(chunk, str): raise RuntimeError('cursor_invalid_json')
                answer += chunk
                if len(answer) > 8000: raise RuntimeError('cursor_invalid_answer')
                if answer.strip() and timings['first_delta_s'] is None: timings['first_delta_s'] = time.monotonic() - start
                if stream and chunk: yield {'snapshot': answer.lstrip(), 'done': False}
        finally:
            # An interrupted turn must not leave stale output for the next request.
            if not completed: self.close()

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            process.poll()  # Reap an exited leader before signalling its owned group.
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            except PermissionError:
                if process.poll() is None: raise
            try: process.wait(timeout=.15)
            except subprocess.TimeoutExpired: pass
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            except PermissionError:
                if process.poll() is None: raise
            process.wait(timeout=3)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()
        if self.directory is not None:
            self.directory.cleanup(); self.directory = None
        self.pending.clear(); self.stderr.clear()
        self.sessions = 0
