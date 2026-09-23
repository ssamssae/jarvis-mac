"""Opt-in local routine adapter. Configuration and credentials stay on the device.

Only exact configured phrases or device + imperative forms can dispatch. Model
output never enters this adapter. Existing executor owns device protocols.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys


def normalize(text):
    return re.sub(r'[\s,.!?，。！？]', '', text).lower()


class SmartHome:
    def __init__(self, config, module=None):
        self.config = config or {}
        self.module = module
        if self.config and module is None:
            path = Path(self.config['module']).expanduser().resolve(strict=True)
            spec = importlib.util.spec_from_file_location('jarvis_local_routines', path)
            self.module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = self.module
            spec.loader.exec_module(self.module)

    def plan(self, text):
        if not self.config:
            return None
        key = normalize(text)
        phrases = {normalize(k): v for k, v in self.config.get('phrases', {}).items()}
        if key in phrases:
            plan = self.module.plan_for_transcript(phrases[key])
            return plan if plan.get('matched') else None
        for alias, device in self.config.get('devices', {}).items():
            match = re.fullmatch(re.escape(normalize(alias)) + r'(?:을|를)?(켜|꺼)(?:줘|주세요|줄래|줄래요|줄수있어|줄수있어요)?', key)
            if not match:
                continue
            power = 'on' if match[1] == '켜' else 'off'
            if device.get('routine'):
                plan = self.module.plan_for_transcript(device['routine'] + (' 켜줘' if power == 'on' else ' 꺼줘'))
                return plan if plan.get('matched') else None
            command = device.get(power)
            if not isinstance(command, list) or not command or not all(isinstance(x,str) for x in command):
                raise ValueError('invalid_device_command')
            return {'matched': True, 'intent':'configured_device',
                    'steps':[self.module.tuya_step(command[0], *command[1:])],
                    'response_text': alias + ' 제어 요청을 전달했어요.'}
        # Timed air-conditioning phrases are opt-in and still parsed by the
        # existing bounded scheduler. No arbitrary schedule or shell command.
        prefix = self.config.get('scheduled_device')
        if prefix and re.fullmatch(re.escape(normalize(prefix)) + r'(?:켜고)?(?:[1-9][0-9]{0,2}|삼십|한시간|두시간)(?:분|시간)?(?:후|뒤|있다가)꺼(?:줘|주세요|줄래)?', key):
            plan = self.module.plan_for_transcript(text)
            return plan if plan.get('matched') else None
        return None

    def execute(self, plan, *, explicit_voice=False, dry_run=False, runner=None):
        water = bool(plan.get('requires_water_confirmation') or any(s.get('water') for s in plan['steps']))
        if water and not explicit_voice:
            return {'status':'blocked', 'intent':plan['intent'], 'answer':'물은 직접 요청하셨을 때만 내보낼 수 있어요.', 'steps':[]}
        def local_runner(argv, env):
            # Never inherit an unrelated turn's water approval. Only this exact,
            # wake-qualified request can grant it; never retry device commands.
            env = dict(env)
            env.pop('TUYA_WATER_CONFIRMED', None)
            if water and explicit_voice:
                env['TUYA_WATER_CONFIRMED'] = '1'
            try:
                result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
            # Do not retain device IDs, addresses, keys or raw provider output.
            return self.module.RunResult(code, '', '')
        try:
            result = self.module.execute_plan(plan, execute=not dry_run, water_confirmed=water and explicit_voice,
                python=self.config['python'], tuya_script=self.config['control_script'],
                scheduler_script=self.config['scheduler_script'], tts_enabled=False,
                runner=runner or local_runner)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            result = {'status':'error', 'executed':[]}
        status = result['status']
        if status == 'ok':
            answer = plan.get('response_text') or '요청한 제어 신호를 보냈어요.'
        elif status == 'dry_run':
            answer = '연결 검사만 했고 기기는 바꾸지 않았어요.'
        else:
            answer = '기기 요청을 끝까지 처리하지 못했어요. 일부 동작했을 수 있어요.'
        return {'status':status, 'intent':plan['intent'], 'answer':answer,
                'steps':[{'type':x['step']['type'], 'status':x['status'],
                          **({'returncode':x['returncode']} if 'returncode' in x else {})}
                         for x in result.get('executed',[])], 'device_state_verified':False}
