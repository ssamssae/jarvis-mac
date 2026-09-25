"""Deterministic, explicitly terminated voice dictation. No model routing."""
import re
import json
import subprocess
import uuid
import time

# Only observed standalone target aliases; do not fuzzy-match dictated content.
NODES = {'헤르메스': 'macbook14', '노트북': 'macbook14', '헬멧스': 'macbook14', 'hermes': 'macbook14', '아테나': 'mac', '아테느': 'mac',
         '볼칸': 'macmini', '볼탄': 'macmini', '불칸': 'macmini'}
ENGINES = {'코덱스': 'codex', 'codex': 'codex', '그록': 'grok', 'grok': 'grok', '커서': 'cursor', 'cursor': 'cursor'}
START = re.compile(r'^\s*(' + '|'.join(NODES) + r')\s*(' + '|'.join(ENGINES) + r')[\s,.!?。！？]*$')
END = re.compile(r'(?:^|\s)(?:엔터|enter)[\s,.!?。！？]*$', re.I)


class Dictation:
    def __init__(self, capture_node=None):
        self.capture_node = capture_node if capture_node in {"mac", "macbook14", "macmini"} else None
        self.start_source = "microphone"
        self.target = None
        self.parts = []
        self.request_id = None
        self.selection_stage = None
        self.selection_engine = None
        self.selection_until = 0.0

    @property
    def selecting(self):
        return self.selection_stage is not None

    def arm_selection(self):
        # Start the reply window after the spoken prompt, not during playback.
        if self.selecting:
            self.selection_until = time.monotonic() + 30

    def select(self, text, *, cancelled=False, start_source="microphone"):
        key = re.sub(r'[\s,.!?。！？]', '', text).lower()
        if self.selecting and cancelled:
            self.cancel()
            return '음성 입력을 취소했습니다.'
        if self.selecting and time.monotonic() >= self.selection_until:
            self.cancel()
            return '선택 시간이 지났어요. 자비스 보이스 스타토로 다시 시작해 주세요.'
        if key in {'보이스스타토', '보이스스타트', '음성입력'}:
            self.cancel()
            self.selection_stage = 'engine'
            self.start_source = 'google-home-matter' if start_source == 'google-home-matter' else 'microphone'
            self.arm_selection()
            return '어디로 연결할까요?'
        if self.selection_stage == 'engine':
            engine = ENGINES.get(key)
            if engine is None:
                return '코덱스, 커서, 그록 중 어디로 연결할까요?'
            self.selection_engine = engine
            self.selection_stage = 'node'
            return '어떤 노드인가요?'
        if self.selection_stage == 'node':
            node = NODES.get(key)
            if node is None:
                return '헤르메스 또는 노트북, 아테나, 볼칸 중 어떤 노드인가요?'
            engine = self.selection_engine
            source = self.start_source
            self.cancel()
            self.start_source = source
            self.target = (node, engine)
            self.request_id = str(uuid.uuid4())
            return '말씀하세요.'
        return None

    def start(self, text):
        match = START.fullmatch(text.lower())
        if not match:
            return False
        self.cancel()
        self.target = (NODES[match[1]], ENGINES[match[2]])
        self.parts = []
        self.request_id = str(uuid.uuid4())
        return True

    def cancel(self):
        self.start_source = "microphone"
        self.target = None
        self.parts = []
        self.request_id = None
        self.selection_stage = None
        self.selection_engine = None
        self.selection_until = 0.0

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
                       text=' '.join(self.parts), start_source=self.start_source, capture_node=self.capture_node)
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
