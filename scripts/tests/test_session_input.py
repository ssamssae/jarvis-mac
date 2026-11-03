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


class ClaudeDeliveryTest(unittest.TestCase):
    def make_bridge(self, screen, transcript, pasted):
        import contextlib
        from types import SimpleNamespace
        transport = SimpleNamespace(composer_lock=contextlib.nullcontext, capture_pane=lambda: screen,
                                    pane_pid=lambda: 1, _paste_prompt_unlocked=pasted.append)
        binder = SimpleNamespace(resolve=lambda: SimpleNamespace(transcript_path=transcript))
        return SimpleNamespace(Config=SimpleNamespace(from_env=lambda: SimpleNamespace(session_sidecar_path=transcript)),
                               TmuxClaudeTransport=lambda config: transport, SessionBinder=lambda config, repl: binder,
                               parse_pane_choice=lambda s: 'choice' if 'Do you want' in s else None,
                               parse_ask_question=lambda s: None,
                               composer_residual_text=lambda s: s.split('>', 1)[1].strip() if '>' in s else '')

    def test_claude_guards_then_pastes_and_verifies_only_the_tail(self):
        from unittest.mock import patch
        import jarvis_session_input as adapter
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory)/'session.jsonl'
            old = json.dumps({'type':'user','message':{'role':'user','content':'본문'}})+'\n'
            transcript.write_text(old)  # an identical older prompt must not count as delivery
            req = dict(id='123', node='mac', engine='claude', origin='microphone', text='본문')
            pasted = []
            with patch.object(adapter, 'load_bridge', return_value=self.make_bridge('Do you want to proceed?', transcript, pasted)):
                self.assertEqual(adapter.local_submit(Path(directory), req), 'busy')
            with patch.object(adapter, 'load_bridge', return_value=self.make_bridge('> draft text', transcript, pasted)):
                self.assertEqual(adapter.local_submit(Path(directory), req), 'composer_occupied')
            bridge = self.make_bridge('> ', transcript, pasted)
            with patch.object(adapter, 'load_bridge', return_value=bridge):
                self.assertEqual(adapter.local_submit(Path(directory), req, probe=True), 'ready')
                self.assertEqual(pasted, [])
                def arrive(_seconds):
                    with transcript.open('a') as handle:
                        handle.write(old)
                with patch.object(adapter.time, 'sleep', arrive):
                    self.assertEqual(adapter.local_submit(Path(directory), req), 'submitted')
            self.assertEqual(pasted, ['본문'])

    def test_claude_without_a_known_transcript_is_unverified_not_resent(self):
        from unittest.mock import patch
        import jarvis_session_input as adapter
        req = dict(id='123', node='zeus', engine='claude', origin='microphone', text='본문')
        pasted = []
        bridge = self.make_bridge('> ', None, pasted)
        bridge.SessionBinder = lambda config, repl: (_ for _ in ()).throw(RuntimeError('ambiguous'))
        with patch.object(adapter, 'load_bridge', return_value=bridge):
            self.assertEqual(adapter.local_submit(Path('/tmp'), req), 'unverified')
        self.assertEqual(pasted, ['본문'])


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
            req = dict(id='123', node='mac', engine='codex', origin='microphone', text='승인')
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
            request = dict(id='123', node='mac', engine='codex', origin='microphone', text='first input')
            pasted = []
            recorded = []
            bound = []
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
                return Path(directory)/'display.json'
            with patch.object(adapter, 'load_bridge', return_value=bridge), patch.dict(sys.modules,
                    {'voice_input_provenance':SimpleNamespace(record_voice_input=record,
                        bind_voice_input_session=lambda receipt,path:bound.append(path))}):
                self.assertEqual(adapter.local_submit(Path(directory), request), 'submitted')
            self.assertEqual(pasted, ['first input'])
            self.assertEqual(recorded, [(None, 23, [])])
            self.assertEqual(bound, [session])

class RetiredTargetTest(unittest.TestCase):
    def test_retired_node_rejected_before_transport(self):
        import io
        from unittest.mock import patch
        import jarvis_session_input as adapter
        request = {'id': '38d3339e-5795-4e52-914d-fd56569e9873', 'node': 'macbook14', 'engine': 'codex', 'text': 'unchanged'}
        with patch.object(sys, 'argv', ['receiver', '--local']), patch.object(sys, 'stdin', io.StringIO(json.dumps(request))), patch.object(adapter, 'local_submit') as submit:
            with self.assertRaisesRegex(ValueError, 'unknown_target'):
                adapter.main()
            submit.assert_not_called()


class CursorFreshSessionTest(unittest.TestCase):
    """2026-10-12 apollo: a Cursor TUI that has never chatted owns no transcript; delivery must still paste."""
    def make_bridge(self, pasted, transcript):
        import contextlib
        from types import SimpleNamespace
        def pick(prior_path=None, pasted_text=None):
            return transcript if transcript.exists() else None
        return SimpleNamespace(composer_lock=contextlib.nullcontext, _tui_capture_pane=lambda: 'idle',
                               _tui_pane_shows_approval_overlay=lambda s: False, _tui_pane_shows_choice_menu=lambda s: False,
                               _tui_pane_shows_model_picker=lambda s: False, _tui_compose_shows_idle_placeholder=lambda s: True,
                               _pick_active_transcript_path=pick, _tui_paste_unlocked=pasted.append)

    def test_cursor_without_transcript_pastes_then_finds_the_new_transcript(self):
        from unittest.mock import patch
        import jarvis_session_input as adapter
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory)/'chat.jsonl'
            pasted = []
            def paste(text):
                pasted.append(text)
                transcript.write_text(json.dumps({'role': 'user', 'content': text}, ensure_ascii=False)+'\n')
            bridge = self.make_bridge(pasted, transcript)
            bridge._tui_paste_unlocked = paste
            req = dict(id='123', node='apollo', engine='cursor', origin='microphone', text='아폴론 첫 입력')
            with patch.object(adapter, 'load_bridge', return_value=bridge):
                self.assertEqual(adapter.local_submit(Path(directory), req), 'submitted')
            self.assertEqual(pasted, ['아폴론 첫 입력'])

    def test_cursor_probe_without_transcript_is_ready(self):
        from unittest.mock import patch
        import jarvis_session_input as adapter
        with tempfile.TemporaryDirectory() as directory:
            bridge = self.make_bridge([], Path(directory)/'none.jsonl')
            req = dict(id='123', node='apollo', engine='cursor', origin='microphone', text='x')
            with patch.object(adapter, 'load_bridge', return_value=bridge):
                self.assertEqual(adapter.local_submit(Path(directory), req, True), 'ready')

    def test_missing_transcript_marks_never_raise(self):
        import jarvis_session_input as adapter
        from unittest.mock import patch
        self.assertEqual(adapter.transcript_mark(None), (None, ''))
        missing = Path(tempfile.gettempdir())/'jarvis-session-input-missing.jsonl'
        missing.unlink(missing_ok=True)
        self.assertEqual(adapter.transcript_mark(missing), (missing, ''))
        clock = iter(range(0, 1000, 30))
        with patch.object(adapter.time, 'monotonic', lambda: next(clock)), patch.object(adapter.time, 'sleep', lambda s: None):
            self.assertEqual(adapter.user_text_seen((missing, ''), '본문'), 'unverified')


class GrokVoicePrefixTest(unittest.TestCase):
    """The Grok bridge mirrors FIFO input to Telegram only when it starts with its [VOICE] prefix (T-260827-024)."""
    def test_grok_fifo_payload_carries_voice_prefix_and_receipt_uses_plain_text(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        import jarvis_session_input as adapter
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'.claude/state'; state.mkdir(parents=True)
            fifo = state/'grok-bridge-test.fifo'; os.mkfifo(fifo)
            reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            history = Path(directory)/'history.jsonl'; history.write_text('')
            bridge = SimpleNamespace(VOICE_PROMPT_PREFIX='[VOICE] ', is_awaiting_human=lambda: False,
                                     clear_awaiting_human=lambda: None, _tui_repl_idle_probe=lambda: True,
                                     model_composer_empty=lambda: True, tui_history_path=lambda: history)
            req = dict(id='123', node='apollo', engine='grok', origin='microphone', text='그록 아폴로나 안녕')
            seen = []
            def fake_seen(mark, text, source=None):
                seen.append((mark[0], text)); return 'submitted'
            try:
                with patch.object(adapter, 'load_bridge', return_value=bridge), \
                     patch.object(adapter.Path, 'home', classmethod(lambda cls: Path(directory))), \
                     patch.object(adapter, 'user_text_seen', fake_seen):
                    status = adapter.local_submit(Path(directory), req)
                payload = os.read(reader, 4096).decode()
            finally:
                os.close(reader)
            self.assertEqual(payload, '[VOICE] 그록 아폴로나 안녕\n')
            self.assertEqual((status, seen), ('submitted', [(history, '그록 아폴로나 안녕')]))


class ClaudeBusyTurnTest(unittest.TestCase):
    """T-261016-045: a prompt pasted into a busy Claude turn is reported queued, not lost.

    The receipt comes only from records Claude appends to this session's JSONL after the paste."""

    TEXT = '자비스 대기열 실측 문장입니다 하나 둘 셋'
    # Recorded 2026-11-03 on athena, Claude Code 2.1.316, prompt pasted during a running Bash call.
    # Fields trimmed to what the receipt reads; order kept.
    ENQUEUE = {'type':'queue-operation', 'operation':'enqueue', 'timestamp':'2026-11-03T00:45:59.936Z',
               'content':TEXT}
    ABSORBED = {'type':'queue-operation', 'operation':'remove', 'timestamp':'2026-11-03T00:46:07.090Z',
                'content':TEXT, 'reason':'absorbed_mid_turn'}
    LANDING = {'type':'attachment', 'attachment':{'type':'queued_command', 'prompt':TEXT, 'commandMode':'prompt',
                                                  'origin':{'kind':'human'}, 'humanTurn':True}}

    def run_submit(self, arrivals, text=TEXT, before=()):
        """Paste once; arrivals[i] is appended during the i-th receipt poll sleep.

        monotonic() jumps 8 s per call, so the 20 s window polls at 8 s and 16 s, then one read follows."""
        import contextlib
        import itertools
        from types import SimpleNamespace
        from unittest.mock import patch
        import jarvis_session_input as adapter
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory)/'session.jsonl'
            transcript.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in before))
            pasted = []
            transport = SimpleNamespace(composer_lock=contextlib.nullcontext, capture_pane=lambda: '> ',
                                        _paste_prompt_unlocked=pasted.append)
            binder = SimpleNamespace(resolve=lambda: SimpleNamespace(transcript_path=transcript))
            bridge = SimpleNamespace(Config=SimpleNamespace(from_env=lambda: SimpleNamespace()),
                                     TmuxClaudeTransport=lambda config: transport,
                                     SessionBinder=lambda config, repl: binder,
                                     parse_pane_choice=lambda s: None, parse_ask_question=lambda s: None,
                                     composer_residual_text=lambda s: '')
            pending = list(arrivals)
            sleeps = []
            def sleep(seconds):
                sleeps.append(seconds)
                batch = pending.pop(0) if pending else []
                with transcript.open('a') as handle:
                    for record in batch:
                        handle.write(json.dumps(record, ensure_ascii=False)+'\n')
            req = dict(id='123', node='mac', engine='claude', origin='microphone', text=text)
            with patch.object(adapter, 'load_bridge', return_value=bridge), \
                 patch.object(adapter.time, 'monotonic', side_effect=itertools.count(0, 8)), \
                 patch.object(adapter.time, 'sleep', sleep):
                status = adapter.local_submit(Path(directory), req)
            self.assertEqual(pasted, [text])  # pasted once, never resent
            return status, sleeps

    def test_enqueued_prompt_is_reported_queued(self):
        status, _ = self.run_submit([[self.ENQUEUE]])
        self.assertEqual(status, 'queued')

    def test_absorbed_landing_is_reported_submitted(self):
        status, _ = self.run_submit([[self.ENQUEUE], [self.ABSORBED, self.LANDING]])
        self.assertEqual(status, 'submitted')

    def test_landing_after_the_last_poll_is_read_once_more(self):
        # The landing arrives during the last sleep of the window (about 19.9 s): no poll sees it,
        # the read after the window does, and no extra wait is added after the window.
        status, sleeps = self.run_submit([[self.ENQUEUE], [self.LANDING]])
        self.assertEqual(status, 'submitted')
        self.assertEqual(len(sleeps), 2)

    def test_a_different_prompt_with_the_same_start_is_not_claimed(self):
        other = self.TEXT + ' 그리고 다른 요청'
        self.assertEqual(other[:16], self.TEXT[:16])
        status, _ = self.run_submit([[dict(self.ENQUEUE, content=other)],
                                     [dict(self.LANDING, attachment=dict(self.LANDING['attachment'], prompt=other))]])
        self.assertEqual(status, 'unverified')

    def test_a_longer_prompt_containing_this_one_is_not_claimed(self):
        landing = dict(self.LANDING, attachment=dict(self.LANDING['attachment'], prompt='승인하지 마'))
        status, _ = self.run_submit([[dict(self.ENQUEUE, content='승인하지 마')], [landing]], text='승인')
        self.assertEqual(status, 'unverified')

    def test_an_older_identical_queued_prompt_is_not_claimed(self):
        # Records written before the paste are outside the tail; nothing new arrives for this paste.
        status, _ = self.run_submit([], before=[self.ENQUEUE])
        self.assertEqual(status, 'unverified')

    def test_prompt_removed_from_the_queue_undelivered_is_not_queued(self):
        dropped = dict(self.ABSORBED, reason='discarded')
        status, _ = self.run_submit([[self.ENQUEUE], [dropped]])
        self.assertEqual(status, 'unverified')

    def test_nested_landing_and_tool_result_are_not_the_prompt(self):
        nested = {'type':'assistant', 'message':{'role':'assistant', 'content':[
            {'type':'tool_use', 'name':'Write', 'input':{'example':self.LANDING, 'queue':self.ENQUEUE}}]}}
        # A command printing exactly the dictated words (cat note.txt) is still not the prompt.
        tool_result = {'type':'user', 'message':{'role':'user', 'content':[
            {'type':'tool_result', 'tool_use_id':'t1', 'content':self.TEXT}]}}
        injected = {'type':'user', 'isMeta':True, 'message':{'role':'user', 'content':self.TEXT}}
        status, _ = self.run_submit([[nested], [tool_result, injected]])
        self.assertEqual(status, 'unverified')

    def test_wrapped_paste_and_spacing_still_match(self):
        wrapped = {'type':'user', 'message':{'role':'user',
                   'content':'\n\n<pasted_content id="a8ec">\n' + self.TEXT.replace(' ', '\n') + '\n</pasted_content id="a8ec">\n'}}
        status, _ = self.run_submit([[wrapped]])
        self.assertEqual(status, 'submitted')

    def test_task_notification_landing_is_not_a_dictated_prompt(self):
        from jarvis_session_input import claude_receipt
        note = {'type':'attachment', 'attachment':{'type':'queued_command', 'commandMode':'task-notification',
                                                  'prompt':'본문'}}
        self.assertEqual(claude_receipt(json.dumps(note, ensure_ascii=False), '본문'), '')
