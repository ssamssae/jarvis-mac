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
                    os.write(w, b'{"wav":"fixture"}\n')
                finally:
                    os.close(w)
                    worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(seen), 2)
                self.assertEqual(seen[0]['intent'], 'work_end')
                self.assertEqual(seen[1], {'wav': 'fixture'})
                self.assertFalse(path.exists())
