import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'google-home/run_work_start.py'
spec = importlib.util.spec_from_file_location('google_start_adapter', path)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class GoogleStartTests(unittest.TestCase):
    def test_uses_existing_work_mode_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'runtime').mkdir()
            (root / 'runtime/jarvis_work_mode.py').write_text(
                "def execute(config):\n    return {'status': 'ok', 'seen': config}\n")
            (root / 'config.json').write_text(json.dumps({
                'work_mode': {'steps': ['start fixture']},
                'work_end': {'steps': ['must not be used']},
            }))
            self.assertEqual(adapter.execute(root), {'status': 'ok', 'seen': {'steps': ['start fixture']}})
