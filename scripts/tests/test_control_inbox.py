import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_control_inbox import events


class InboxTests(unittest.TestCase):
    def test_malformed_native_lines_do_not_end_following_voice_events(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as root:
            r, w = os.pipe()
            os.write(w, b'not-json\n[]\nnull\n\xff\n{"wav":"next-valid"}\n')
            os.close(w)
            with os.fdopen(r) as source:
                self.assertEqual(list(events(source, root)), [{'wav': 'next-valid'}])
            self.assertFalse((Path(root) / 'work-end.sock').exists())

    def test_local_socket_rejects_stale_wrong_and_duplicate_requests(self):
        # macOS AF_UNIX paths have a small length limit.
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            root = Path(d)
            r, w = os.pipe()
            seen = []
            with os.fdopen(r) as source:
                worker = threading.Thread(target=lambda: seen.extend(events(source, root)))
                worker.start()
                path = root / 'work-end.sock'
                def send(payload):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(2)
                        client.connect(str(path))
                        client.sendall((json.dumps(payload) + '\n').encode())
                        return client.recv(64)
                try:
                    for _ in range(100):
                        try:
                            if send({'intent': 'probe'}) == b'rejected\n': break
                        except (FileNotFoundError, ConnectionRefusedError):
                            time.sleep(.01)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(send({'intent': 'execute', 'created_at': time.time()}), b'rejected\n')
                    self.assertEqual(send({'intent': 'work_end', 'created_at': time.time()-10}), b'rejected\n')
                    spec = importlib.util.spec_from_file_location('end_request', Path(__file__).resolve().parents[1] / 'google-home/request_work_end.py')
                    adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
                    self.assertTrue(adapter.request(root))
                    self.assertFalse(adapter.request(root))
                    spec = importlib.util.spec_from_file_location('dictation_request', Path(__file__).resolve().parents[1] / 'google-home/request_dictation.py')
                    input_adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(input_adapter)
                    self.assertTrue(input_adapter.request(root))
                    self.assertFalse(input_adapter.request(root))
                    for intent in ('scene_game', 'scene_sleep', 'scene_away', 'work_start'):
                        payload = {'source':'butler-remote', 'intent':intent, 'created_at':time.time()}
                        self.assertEqual(send(payload), b'queued\n')
                        self.assertEqual(send(payload), b'rejected\n')
                    for intent in ('water', 'execute', 'scene_arbitrary', ['scene_game']):
                        self.assertEqual(send({'source':'butler-remote', 'intent':intent,
                                               'created_at':time.time()}), b'rejected\n')
                    self.assertEqual(send({'intent':'scene_game','created_at':time.time()}), b'rejected\n')
                    os.write(w, b'{"wav":"fixture"}\n')
                finally:
                    os.close(w)
                    worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(seen), 7)
                self.assertEqual(seen[0]['intent'], 'work_end')
                self.assertEqual(seen[1]['intent'], 'dictation_start')
                self.assertEqual([row['intent'] for row in seen[2:6]],
                                 ['scene_game','scene_sleep','scene_away','work_start'])
                self.assertTrue(all(row['source'] == 'butler-remote' for row in seen[2:6]))
                self.assertEqual(seen[6], {'wav': 'fixture'})
                self.assertFalse(path.exists())
