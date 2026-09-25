import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_session_input import codex_composer_empty, user_text_seen


class SessionInputTest(unittest.TestCase):
    def test_placeholder_must_be_dim_not_typed(self):
        self.assertTrue(codex_composer_empty('› \x1b[2mExplain this codebase\x1b[0m'))
        self.assertTrue(codex_composer_empty('› '))
        self.assertFalse(codex_composer_empty('› Explain this codebase'))
        self.assertFalse(codex_composer_empty('› \x1b[2mHint\x1b[0m draft'))
        self.assertFalse(codex_composer_empty('shell $'))

    def test_receipt_reads_user_text_with_quotes_and_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'events.jsonl'
            text = '따옴표 "본문"\n다음 줄'
            p.write_text(json.dumps({'type':'response_item','payload':{'role':'user',
                         'content':[{'type':'input_text','text':text}]}}, ensure_ascii=False)+'\n')
            self.assertEqual(user_text_seen((p, 0), text), 'submitted')
