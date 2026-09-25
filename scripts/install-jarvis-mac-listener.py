#!/usr/bin/env python3
"""Install the explicitly requested local Jarvis login app; never touch other services."""
import argparse
import datetime
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import subprocess

from jarvis_mac_voice import resolve_say_voice

LABEL = 'com.ssamssae.jarvis-mac-oss'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--voice', help='Exact installed macOS say voice; omitted preserves existing choice or defaults to Yuna')
    parser.add_argument('--cast-name', required=True, help='Exact Nest friendly name')
    parser.add_argument('--model', type=Path, required=True, help='User-provided whisper.cpp model')
    parser.add_argument('--whisper-cli', default='whisper-cli', help='Executable path or PATH name')
    parser.add_argument('--stt-worker', type=Path, help='Optional compatible worker executable (model argument)')
    parser.add_argument('--cursor-binary', type=Path, default=Path.home()/'.local/bin/agent')
    args = parser.parse_args()
    if sys.platform != 'darwin': parser.error('macOS is required')
    if not args.cast_name.strip(): parser.error('--cast-name cannot be blank')
    args.model = args.model.expanduser().resolve()
    if not args.model.is_file(): parser.error('--model must be an existing model file')
    whisper = shutil.which(args.whisper_cli)
    if not args.stt_worker and not whisper: parser.error('whisper-cli not found; pass --whisper-cli')
    if args.stt_worker:
        args.stt_worker = args.stt_worker.expanduser().resolve()
        if not os.access(args.stt_worker, os.X_OK): parser.error('--stt-worker must be executable')
    args.cursor_binary = args.cursor_binary.expanduser().resolve()
    if not os.access(args.cursor_binary, os.X_OK): parser.error('Cursor CLI not found; pass --cursor-binary')
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11+ required for the isolated Cast runtime')
    os.umask(0o077)
    home = Path.home()
    root = home/'Library/Application Support/JarvisMacOSS'
    app = home/'Applications/JarvisMacOSS.app'
    plist = home/'Library/LaunchAgents'/f'{LABEL}.plist'
    scripts = Path(__file__).resolve().parent
    existing = json.loads((root/'config.json').read_text()) if (root/'config.json').exists() else {}
    try: selected_voice = resolve_say_voice(args.voice, existing.get('voice'))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc) if isinstance(exc, ValueError) else 'cannot_list_installed_say_voices')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if subprocess.run(['pgrep','-x','JarvisMacOSS'],capture_output=True).returncode == 0:
        raise SystemExit('JarvisMacOSS is running; quit its menu before updating')
    stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
    backup = root/'backups'/stamp
    for path in (app, plist, root/'config.json'):
        if path.exists():
            backup.mkdir(parents=True, exist_ok=True)
            if path.is_dir(): shutil.copytree(path, backup/path.name)
            else: shutil.copy2(path, backup/path.name)
    runtime = root/'runtime'; runtime.mkdir(exist_ok=True)
    for name in ('jarvis_mac_voice.py','jarvis_cursor_qa.py','jarvis_cursor_acp.py','jarvis_mac_listener.py','jarvis_control_inbox.py','whisper_cpp_worker.py','jarvis_smart_home.py','jarvis_weather.py','jarvis_status_light.py','jarvis_work_mode.py','jarvis_work_end.py','jarvis_display_brightness.py'):
        shutil.copy2(scripts/name, runtime/name)
    venv = root/'venv'
    if not (venv/'bin/python3').exists():
        subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
    python = venv/'bin/python3'
    # Existing voice pipeline optional Cast dependency, isolated from system Python.
    subprocess.run([str(python),'-m','pip','install','pychromecast==14.0.9'],check=True)
    contents = app/'Contents'
    (contents/'MacOS').mkdir(parents=True, exist_ok=True)
    shutil.copy2(scripts/'native/jarvis_mac_listener.Info.plist', contents/'Info.plist')
    subprocess.run(['xcrun','swiftc','-swift-version','5','-O',str(scripts/'native/jarvis_mac_listener.swift'),
                    '-o',str(contents/'MacOS/JarvisMacOSS')],check=True)
    subprocess.run(['codesign','--force','--sign','-','--identifier',LABEL,str(app)],check=True)
    config = {'python':str(python),'controller':str(runtime/'jarvis_mac_listener.py'),
              'state_dir':str(root),'cast_name':args.cast_name,'model':str(args.model),
              'whisper_cli':str(whisper or ''),'cursor_binary':str(args.cursor_binary),
              'voice':selected_voice}
    if 'smart_home' in existing: config['smart_home'] = existing['smart_home']
    if 'weather' in existing: config['weather'] = existing['weather']
    if 'status_light' in existing: config['status_light'] = existing['status_light']
    if 'work_mode' in existing: config['work_mode'] = existing['work_mode']
    if 'work_end' in existing: config['work_end'] = existing['work_end']
    if args.stt_worker: config['stt_worker'] = str(args.stt_worker)
    (root/'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
    (root/'config.json').chmod(0o600)
    logs = root/'logs'; logs.mkdir(exist_ok=True)
    # GUI LaunchServices gives this native app its own mic identity and login session.
    # RunAtLoad only: quitting must remain quit, never an auto-relaunch loop.
    job = {'Label':LABEL,'ProgramArguments':['/usr/bin/open','-g','-a',str(app)],
           'RunAtLoad':True,'LimitLoadToSessionType':'Aqua',
           'StandardOutPath':str(logs/'launch.log'),'StandardErrorPath':str(logs/'launch-error.log')}
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(job))
    plist.chmod(0o600)
    if args.start:
        domain = f'gui/{os.getuid()}'
        found = subprocess.run(['launchctl','print',f'{domain}/{LABEL}'],capture_output=True).returncode == 0
        if found: subprocess.run(['launchctl','bootout',f'{domain}/{LABEL}'],check=True)
        subprocess.run(['launchctl','bootstrap',domain,str(plist)],check=True)
    print(json.dumps({'app':str(app),'config':str(root/'config.json'),'launch_agent':str(plist),'started':args.start}))

if __name__ == '__main__': main()
