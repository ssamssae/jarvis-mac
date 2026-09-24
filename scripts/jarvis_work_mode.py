"""Exact work-start routine. Executes only trusted, locally configured argv arrays."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess


def matches(text):
    return re.sub(r'[\s,.!?，。！？]','',text) in {'일하자','일시작하자','작업시작하자'}


def execute(config, runner=subprocess.run):
    if not config or not isinstance(config.get('steps'),list) or not 1 <= len(config['steps']) <= 4:
        return {'intent':'work_mode','status':'unconfigured','answer':'일하자 루틴이 아직 설정되지 않았어요.','steps':[]}
    def step(item):
        name=item.get('name','기기');kind=item.get('kind');argv=item.get('argv')
        if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv):
            return {'name':name,'status':'unconfigured'}
        if kind not in {'brightness','wake'}:return {'name':name,'status':'unconfigured'}
        try:
            process=runner(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=15)
            if process.returncode:return {'name':name,'status':'failed'}
            if kind=='brightness':
                data=json.loads(process.stdout)
                if data.get('verified') is not True or data.get('level') != 6:return {'name':name,'status':'unverified'}
                return {'name':name,'status':'brightness_verified','level':6}
            marker=item.get('success_marker')
            if not isinstance(marker,str) or not marker or marker not in process.stdout:
                return {'name':name,'status':'unverified'}
            # A magic packet receipt is not proof of physical boot.
            return {'name':name,'status':'wake_sent'}
        except (OSError,ValueError,subprocess.SubprocessError):
            return {'name':name,'status':'failed'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(step,config['steps']))
    bright=[x['name'] for x in results if x['status']=='brightness_verified']
    wakes=[x['name'] for x in results if x['status']=='wake_sent']
    failed=[x['name'] for x in results if x['status'] not in {'brightness_verified','wake_sent'}]
    parts=[]
    if bright:parts.append('·'.join(bright)+' 화면 밝기를 6칸으로 맞췄어요.')
    if wakes:parts.append('·'.join(wakes)+'에 전원 켜기 신호를 보냈어요. 부팅 완료는 아직 확인하지 못했어요.')
    if failed:parts.append('·'.join(failed)+'는 처리하지 못했어요.')
    return {'intent':'work_mode','status':'partial' if failed else 'ok','answer':('작전 개시. 시스템 기동을 요청했습니다.' if not failed and len(bright)==2 and len(wakes)==2 else ' '.join(parts)),'steps':results}
