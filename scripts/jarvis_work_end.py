"""One-use, expiring voice confirmation for a locally configured work-end routine."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess


def normalize(text):
    return re.sub(r'[\s,.!?，。！？]', '', text)


class Confirmation:
    window = 12

    def __init__(self):
        self.until = 0
        self.prompt_finished_wall = 0

    def arm(self, now, wall):
        self.until = now + self.window
        self.prompt_finished_wall = wall

    def cancel(self):
        self.until = 0

    def accept(self, text, now, speech_started_wall):
        word = normalize(text)
        pending = now < self.until
        self.cancel()  # Consume before execution; no duplicate yes or later replay.
        if word == '일끝':
            return 'prompt'
        if pending:
            if speech_started_wall < self.prompt_finished_wall:
                return 'cancel'
            return 'execute' if word in {'예', '네'} else 'cancel'
        return None


PROMPT = '맥북 두 대의 밝기를 0칸으로 내리고, 라이덴과 테미스를 종료할까요? 저장 중인 작업이 없다면 예, 취소하려면 아니요라고 말씀해 주세요.'
CANCELLED = '작업 종료를 취소했어요.'


def execute(config, runner=subprocess.run):
    steps = config.get('steps') if isinstance(config, dict) else None
    if not isinstance(steps, list) or len(steps) != 4:
        return {'status': 'unconfigured', 'answer': '일 끝 루틴이 아직 설정되지 않았어요.', 'steps': []}

    def step(item):
        name = item.get('name', '기기'); kind = item.get('kind'); argv = item.get('argv')
        if kind not in {'brightness', 'shutdown'} or not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            return {'name': name, 'status': 'unconfigured'}
        try:
            process = runner(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
            if process.returncode:
                return {'name': name, 'status': 'unverified'}
            if kind == 'brightness':
                data = json.loads(process.stdout)
                if data.get('verified') is not True or data.get('level') != 0:
                    return {'name': name, 'status': 'unverified'}
                return {'name': name, 'status': 'brightness_verified', 'level': 0}
            if 'JARVIS_SHUTDOWN_ACCEPTED' not in process.stdout:
                return {'name': name, 'status': 'unverified'}
            return {'name': name, 'status': 'shutdown_requested'}
        except (OSError, ValueError, subprocess.SubprocessError):
            return {'name': name, 'status': 'unverified'}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(step, steps))
    bright = [x['name'] for x in results if x['status'] == 'brightness_verified']
    shutdown = [x['name'] for x in results if x['status'] == 'shutdown_requested']
    failed = [x['name'] for x in results if x['status'] not in {'brightness_verified', 'shutdown_requested'}]
    parts = []
    if bright: parts.append('·'.join(bright) + ' 화면 밝기를 0칸으로 맞췄어요.')
    if shutdown: parts.append('·'.join(shutdown) + '에 정상 종료를 요청했어요.')
    if failed: parts.append('·'.join(failed) + '는 처리 결과를 확인하지 못했어요.')
    return {'status': 'partial' if failed else 'ok', 'answer': ' '.join(parts), 'steps': results}
