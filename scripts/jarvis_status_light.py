"""Optional local status light. Network work stays off the voice controller thread."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

COLORS = {'blue':'00f003e8000a', 'green':'007803e8000a', 'yellow':'003c03e8000a'}
FIELDS = ('1','2','3','4','5')


def restore_patch(original, owned, current):
    # Normal standby is OFF. Never overwrite an external change to the bulb.
    return {'1':False} if current == owned and current.get('1') is True else {}


class StatusLight:
    def __init__(self, config, root, backend=None):
        self.config = config
        self.root = Path(root)
        self.journal = self.root/'status-light-restore.json'
        self.receipt = self.root/'status-light.json'
        self.backend = backend or self._backend
        self.original = self.owned = None
        self.suspended = False
        self.deadline = None
        self.events = queue.Queue()
        self.thread = None
        if config:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _backend(self, patch=None):
        result = subprocess.run([self.config['python'], str(Path(__file__).resolve()), '--device'],
            input=json.dumps({'config':self.config, 'patch':patch}), text=True,
            capture_output=True, timeout=8)
        data = json.loads(result.stdout)
        if result.returncode or data.get('error'):
            raise RuntimeError(data.get('code') or 'status_light_device_failed')
        return data['dps']

    def _save(self, path, data):
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data));tmp.chmod(0o600);tmp.replace(path)

    def _note(self, state, **extra):
        self._save(self.receipt, {'state':state, 'updated_wall':time.time(), **extra})

    def _restore(self):
        self.deadline = None
        if self.original is not None:
            current = self.backend()
            patch = restore_patch(self.original, self.owned, current)
            if patch:
                after = self.backend(patch)
                if any(after.get(k) != v for k,v in patch.items()):
                    after = self.backend()  # Allow delayed Zigbee state reporting without replay.
                if any(after.get(k) != v for k,v in patch.items()):
                    raise RuntimeError('status_light_restore_unverified')
            self.journal.unlink(missing_ok=True)
            self.original = self.owned = None
            self._note('restored')

    def _color(self, color, ttl):
        if self.suspended:
            return
        current = self.backend()
        if self.original is not None and current != self.owned:
            self._restore()
            self.suspended = True
            self._note('external_change_preserved')
            return
        if self.original is None:
            self.original = dict(current)
        target = dict(current, **{'1':True,'2':'colour','5':COLORS[color]})
        # Journal intent before writing, so a later process can recover after a crash.
        self.owned = target
        self._save(self.journal, {'original':self.original, 'owned':self.owned})
        actual = self.backend({'1':True,'2':'colour','5':COLORS[color]})
        if actual != target:
            raise RuntimeError('status_light_color_unverified')
        self.deadline = time.monotonic()+ttl if ttl else None
        self._note(color, verified=True)

    def _run(self):
        try:
            if self.journal.exists():
                saved = json.loads(self.journal.read_text())
                self.original, self.owned = saved['original'], saved['owned']
                self._restore()
        except Exception:
            self.suspended = True
            self._note('recovery_failed')
        while True:
            wait = max(0, self.deadline-time.monotonic()) if self.deadline else None
            try:
                color, ttl, done, outcome = self.events.get(timeout=wait)
            except queue.Empty:
                color, ttl, done, outcome = 'restore', None, None, {}
            try:
                if color in ('restore','close'):
                    self._restore()
                    self.suspended = False
                else:
                    self._color(color, ttl)
                outcome['ok'] = True
            except Exception as exc:
                self.deadline = time.monotonic()+5 if self.original is not None else None
                self.suspended = True
                outcome['ok'] = False
                self._note('error', error_type=type(exc).__name__, code=str(exc) if str(exc) in {'unsupported_light','light_write_failed','status_light_restore_unverified','status_light_color_unverified','status_light_device_failed'} else 'device_error')
            finally:
                if done:done.set()
            if color == 'close':return

    def show(self, color, ttl=30):
        if self.thread:self.events.put((color,ttl,None,{}))

    def release(self):
        if not self.thread:return True
        done=threading.Event();result={}
        self.events.put(('restore',None,done,result))
        return done.wait(20) and result.get('ok',False)

    def close(self):
        if self.thread:
            done=threading.Event();self.events.put(('close',None,done,{}))
            done.wait(20);self.thread.join(timeout=1)


def device_request(request):
    # Runs only under the user's existing local Tuya Python environment.
    import tinytuya
    config=request['config']
    local=json.loads(Path(config['map']).expanduser().read_text())
    dev=local['devices'][config['device']];gw=local['gateways'][dev['gateway']]
    d=tinytuya.Device(dev['dev_id'],address=gw['ip'],local_key=gw['local_key'],
                     version=float(gw.get('version',3.3)),node_id=dev.get('node_id'))
    d.set_socketTimeout(2);d.set_socketRetryLimit(1)
    try:
        patch=request.get('patch')
        if patch:
            patch = dict(patch)
            patch.setdefault('1', d.status()['dps']['1'])
            if any(k not in FIELDS for k in patch):raise ValueError('invalid_light_field')
            # This Zigbee bulb drops fields in combined writes. Set individually,
            # with power-off first and power-on last to avoid bright flashes.
            order = ['1'] if patch.get('1') is False else []
            order += ['5','3','4','2']  # Color/brightness writes also change mode.
            order.append('1')  # Color writes may switch this bulb on implicitly.
            for key in order:
                if key not in patch:continue
                result=d.set_value(key,patch[key])
                if isinstance(result,dict) and result.get('Error'):raise RuntimeError('light_write_failed')
                time.sleep(.08)
        result=d.status()
        values=result['dps']
        if patch:
            for _ in range(3):
                if all(values.get(k)==v for k,v in patch.items()):break
                time.sleep(.1)
                values=d.status()['dps']
        if not all(k in values for k in FIELDS):raise ValueError('unsupported_light')
        return {'dps':{k:values[k] for k in FIELDS}}
    finally:d.close()


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['--device']:raise ValueError('invalid_mode')
        print(json.dumps(device_request(json.loads(sys.stdin.read(16000)))))
    except Exception as exc:
        code=str(exc) if str(exc) in {'unsupported_light','light_write_failed'} else 'status_light_device_failed'
        print(json.dumps({'error':'status_light_device_failed','code':code}));sys.exit(1)
