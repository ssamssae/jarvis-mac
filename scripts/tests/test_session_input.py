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


class ProvenanceDeliveryTest(unittest.TestCase):
    def test_metadata_precedes_unchanged_prompt_and_failed_paste_removes_it(self):
        import contextlib
        from types import SimpleNamespace
        from unittest.mock import patch
        import jarvis_session_input as adapter
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)/'session.jsonl'
            session.write_text('')
            marker = Path(directory)/'receipt.json'
            req = dict(id='123', node='macbook14', engine='codex', origin='microphone', text='승인')
            calls = []
            def record(request, path, **kwargs):
                calls.append('metadata')
                self.assertEqual(request, req)
                marker.write_text('{}')
                return marker
            def paste(text):
                calls.append('paste')
                self.assertTrue(marker.exists())
                self.assertEqual(text, req['text'])
                raise RuntimeError('transport unavailable')
            transport = SimpleNamespace(composer_lock=contextlib.nullcontext,
                capture_visible_screen=lambda:'idle', tmux=lambda *args:SimpleNamespace(stdout='› '),
                pane_pid=lambda:1, _paste_prompt_unlocked=paste)
            bridge = SimpleNamespace(TmuxTransport=lambda config:transport,
                parse_approval_prompt=lambda screen:None, parse_choice_prompt=lambda screen:None,
                session_file_from_descendants=lambda pid:session)
            with patch.object(adapter, 'load_bridge', return_value=bridge), patch.dict(sys.modules,
                    {'voice_input_provenance':SimpleNamespace(record_voice_input=record)}):
                self.assertEqual(adapter.local_submit(Path(directory), req, probe=True), 'ready')
                self.assertEqual(calls, [])
                with self.assertRaises(RuntimeError): adapter.local_submit(Path(directory), req)
            self.assertEqual(calls, ['metadata','paste'])
            self.assertFalse(marker.exists())

class FreshSessionInputTest(unittest.TestCase):
    def test_first_input_is_pasted_once_before_session_exists(self):
        import contextlib
        from types import SimpleNamespace
        from unittest.mock import patch
        import jarvis_session_input as adapter
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)/'new.jsonl'
            request = dict(id='123', node='macbook14', engine='codex', origin='microphone', text='first input')
            pasted = []
            recorded = []
            def paste(text):
                pasted.append(text)
                session.write_text(json.dumps({'payload': {'role': 'user', 'content': text}})+'\n')
            transport = SimpleNamespace(composer_lock=contextlib.nullcontext,
                capture_visible_screen=lambda:'idle', tmux=lambda *args:SimpleNamespace(stdout='› '),
                pane_pid=lambda:23, _paste_prompt_unlocked=paste)
            bridge = SimpleNamespace(TmuxTransport=lambda config:transport,
                parse_approval_prompt=lambda screen:None, parse_choice_prompt=lambda screen:None,
                session_file_from_descendants=lambda pid:session if session.exists() else None)
            def record(req, path, **kwargs):
                recorded.append((path, kwargs['pane_pid'], list(pasted)))
            with patch.object(adapter, 'load_bridge', return_value=bridge), patch.dict(sys.modules,
                    {'voice_input_provenance':SimpleNamespace(record_voice_input=record)}):
                self.assertEqual(adapter.local_submit(Path(directory), request), 'submitted')
            self.assertEqual(pasted, ['first input'])
            self.assertEqual(recorded, [(None, 23, [])])
