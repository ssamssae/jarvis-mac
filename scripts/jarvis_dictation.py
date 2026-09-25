"""Deterministic, explicitly terminated voice dictation. No model routing."""
import re
import json
import subprocess
import uuid

# Only observed standalone target aliases; do not fuzzy-match dictated content.
NODES = {'헤르메스': 'macbook14', '헬멧스': 'macbook14', 'hermes': 'macbook14', '아테나': 'mac', '아테느': 'mac',
         '볼칸': 'macmini', '볼탄': 'macmini', '불칸': 'macmini'}
ENGINES = {'코덱스': 'codex', 'codex': 'codex', '그록': 'grok', '커서': 'cursor'}
START = re.compile(r'^\s*(' + '|'.join(NODES) + r')\s*(' + '|'.join(ENGINES) + r')[\s,.!?。！？]*$')
END = re.compile(r'(?:^|\s)(?:엔터|enter)[\s,.!?。！？]*$', re.I)


class Dictation:
    def __init__(self):
        self.target = None
        self.parts = []
        self.request_id = None

    def start(self, text):
        match = START.fullmatch(text.lower())
        if not match:
            return False
        self.target = (NODES[match[1]], ENGINES[match[2]])
        self.parts = []
        self.request_id = str(uuid.uuid4())
        return True

    def cancel(self):
        self.target = None
        self.parts = []
        self.request_id = None

    def accept(self, text):
        if not self.target:
            raise ValueError('dictation_not_started')
        text = text.strip()
        match = END.search(text)
        body = text[:match.start()].strip() if match else text
        if body:
            self.parts.append(body)
        if not match:
            return 'collecting', None
        if not self.parts:
            return 'empty', None
        request = dict(id=self.request_id, node=self.target[0], engine=self.target[1], origin='microphone',
                       text=' '.join(self.parts))
        self.cancel()
        return 'submit', request


def deliver(config, request, runner=subprocess.run):
    """Only trusted local argv from config; speech always travels on stdin."""
    argv = (config or {}).get('argv')
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
        return {'status': 'unconfigured'}
    try:
        result = runner(argv, input=json.dumps(request, ensure_ascii=False),
                        text=True, capture_output=True, timeout=45)
        if result.returncode:
            return {'status': 'failed'}
        data = json.loads(result.stdout)
        if data.get('id') != request['id']:
            return {'status': 'unverified'}
        return {'status': data.get('status', 'unverified')}
    except (OSError, ValueError, subprocess.SubprocessError):
        # An ambiguous transport failure must never trigger an automatic retry.
        return {'status': 'unknown'}
