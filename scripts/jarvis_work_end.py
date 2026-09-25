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
        self.japanese = False

    def arm(self, now, wall, *, japanese=False):
        self.until = now + self.window
        self.prompt_finished_wall = wall
        self.japanese = japanese

    def cancel(self):
        self.until = 0

    def accept(self, text, now, speech_started_wall):
        word = normalize(text)
        pending = now < self.until
        self.cancel()  # Consume before execution; no duplicate yes or later replay.
        # Cancellation can only remove authority, including the observed ASR typo.
        if word in {'아니요', '아니오', '취소해줘', '취소해주세요'} or re.fullmatch(r'(?:취소|치솔)+', word):
            return 'cancel'
        # Observed Korean STT renderings of shigoto owari only request confirmation.
        if word in {'시고토오와리', 'しごとおわり', 'しごと終わり', '仕事終わり',
                    '시골토의끝', '직업끝'}:
            return 'prompt'
        if pending:
            if speech_started_wall < self.prompt_finished_wall:
                return 'cancel'
            allowed = {'はい'} if self.japanese else {'예', '네'}
            return 'execute' if word in allowed else 'cancel'
        return None


PROMPT = '오늘의 작전을 종료할까요?'
GOOGLE_PROMPT = '오늘의 작전을 종료할까요? 하이라고 답해주세요.'
CANCELLED = '작전 종료 취소. 대기하겠습니다.'


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
    failed = [x['name'] for x in results if x['status'] not in {'brightness_verified', 'shutdown_requested'}]
    answer = '작전 종료 절차를 시작합니다.'
    return {'status': 'partial' if failed else 'ok', 'answer': answer, 'steps': results}
