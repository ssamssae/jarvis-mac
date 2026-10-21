"""Shuttle requests: other local scripts' sounds and helpers through the listener process."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis_shuttle as shuttle
from jarvis_control_inbox import events
import jarvis_mac_listener as app

KEY = 'a' * 32
WAV = b'RIFF' + b'\0' * 60


def request(intent='cast_wav', now=None, **extra):
    now = time.time() if now is None else now
    return {'source': shuttle.SOURCE, 'intent': intent, 'id': KEY,
            'created_at': now, 'expires': now + 10, **extra}


class FakeSession:
    def __init__(self, directory, fail=None):
        self.directory = Path(directory); self.fail = fail
        self.played = []; self.invalidated = 0; self.cast = self

    def connect(self, receipt):
        return self

    def play(self, path, on_started):
        path = Path(path)
        self.played.append((path.name, path.parent == self.directory, path.read_bytes()))
        if self.fail:
            raise RuntimeError(self.fail)
        return {'finished': True, 'cast_start_s': 0.25, 'finished_at': 1.0}

    def invalidate(self):
        self.invalidated += 1


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp')
        self.root = Path(self.temp.name)
        shuttle.inbox(self.root).mkdir()
        (shuttle.inbox(self.root) / (KEY + '.wav')).write_bytes(WAV)

    def tearDown(self):
        self.temp.cleanup()

    def test_cast_request_names_only_its_own_inbox_file(self):
        event = shuttle.validate_request(request(), self.root)
        self.assertEqual(event['wav'], str(shuttle.inbox(self.root) / (KEY + '.wav')))
        self.assertEqual(event['intent'], 'cast_wav')
        for bad in ('../' + 'a' * 29, 'A' * 32, 'a' * 31, 7, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'invalid_id'):
                shuttle.validate_request(request(id=bad), self.root)

    def test_rejects_other_sources_intents_and_expired_requests(self):
        now = time.time()
        cases = [({'source': 'google-home-matter'}, 'invalid_source'),
                 ({'intent': 'run'}, 'unknown_intent'),
                 ({'intent': ['cast_wav']}, 'unknown_intent'),
                 ({'expires': now}, 'invalid_expiry'),
                 ({'expires': now + 301}, 'invalid_expiry'),
                 ({'expires': True}, 'invalid_expiry'),
                 ({'created_at': now - 20, 'expires': now - 1}, 'expired'),
                 ({'created_at': now + 60, 'expires': now + 70}, 'expired')]
        for change, code in cases:
            with self.subTest(code=code, change=change), self.assertRaisesRegex(ValueError, code):
                shuttle.validate_request({**request(now=now), **change}, self.root, now=now)

    def test_audio_must_be_a_regular_file_of_sane_size(self):
        path = shuttle.inbox(self.root) / (KEY + '.wav')
        path.write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, 'invalid_audio_file'):
            shuttle.validate_request(request(), self.root)
        path.unlink(); (self.root / 'outside.wav').write_bytes(WAV); path.symlink_to(self.root / 'outside.wav')
        with self.assertRaisesRegex(ValueError, 'missing_audio'):
            shuttle.validate_request(request(), self.root)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing_audio'):
            shuttle.validate_request(request(), self.root)

    def test_helper_request_carries_a_name_and_small_payload_only(self):
        event = shuttle.validate_request(request('helper', name='approval_light', payload={'action': 'notify'}), self.root)
        self.assertEqual((event['name'], event['payload']), ('approval_light', {'action': 'notify'}))
        for change, code in [({'name': '/bin/sh'}, 'invalid_helper'), ({'name': 'Light'}, 'invalid_helper'),
                             ({'name': 'x', 'payload': ['a']}, 'invalid_payload'),
                             ({'name': 'x', 'payload': {'k': 'v' * 2000}}, 'invalid_payload')]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, code):
                shuttle.validate_request(request('helper', **change), self.root)


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp')
        self.root = Path(self.temp.name)
        self.box = shuttle.inbox(self.root); self.box.mkdir()
        (self.root / 'speech').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def cast_event(self, **extra):
        (self.box / (KEY + '.wav')).write_bytes(WAV)
        return {**shuttle.validate_request(request(), self.root), **extra}

    def result(self):
        return json.loads((self.box / (KEY + '.json')).read_text())

    def test_cast_plays_from_the_session_directory_and_cleans_up(self):
        session = FakeSession(self.root / 'speech')
        shuttle.serve(self.cast_event(), self.root, {}, session)
        self.assertEqual(session.played, [('shuttle-' + KEY + '.wav', True, WAV)])
        self.assertEqual(self.result(), {'id': KEY, 'intent': 'cast_wav', 'finished': True,
                                         'cast_start_s': 0.25, 'result': 'pass'})
        self.assertEqual(list((self.root / 'speech').iterdir()), [])
        self.assertEqual(sorted(p.name for p in self.box.iterdir()), [KEY + '.json'])

    def test_cast_error_code_is_reported_and_connection_reset_without_replay(self):
        session = FakeSession(self.root / 'speech', fail='existing_media_preserved')
        shuttle.serve(self.cast_event(), self.root, {}, session)
        self.assertEqual(len(session.played), 1)
        self.assertEqual(session.invalidated, 1)
        self.assertEqual((self.result()['result'], self.result()['error_code']), ('error', 'existing_media_preserved'))
        self.assertFalse((self.box / (KEY + '.wav')).exists())

    def test_free_text_errors_are_not_copied_into_results(self):
        session = FakeSession(self.root / 'speech', fail='device said: secret 192.0.2.1')
        shuttle.serve(self.cast_event(), self.root, {}, session)
        self.assertEqual(self.result()['error_code'], 'shuttle_failed')

    def test_expired_request_is_never_played(self):
        session = FakeSession(self.root / 'speech')
        shuttle.serve(self.cast_event(expires=time.time() - 1), self.root, {}, session)
        self.assertEqual(session.played, [])
        self.assertEqual(self.result()['error_code'], 'shuttle_expired')
        self.assertFalse((self.box / (KEY + '.wav')).exists())

    def test_helper_runs_only_the_configured_argv_with_payload_on_stdin(self):
        config = {'shuttle': {'helpers': {'echo': [sys.executable, '-c',
                  'import json,sys; print("noise"); print(json.dumps({"got": json.load(sys.stdin)}))']}}}
        event = shuttle.validate_request(request('helper', name='echo', payload={'action': 'notify'}), self.root)
        shuttle.serve(event, self.root, config, FakeSession(self.root / 'speech'))
        self.assertEqual(self.result()['output'], {'got': {'action': 'notify'}})
        self.assertEqual(self.result()['result'], 'pass')

    def test_unconfigured_failing_or_slow_helpers_are_errors(self):
        config = {'shuttle': {'helpers': {'fail': [sys.executable, '-c', 'import sys; sys.stderr.write("key=1"); sys.exit(3)'],
                                          'bad': 'echo hi',
                                          'slow': [sys.executable, '-c', 'import time; time.sleep(5)']}}}
        for name, code, life in [('missing', 'helper_not_configured', 10), ('bad', 'helper_not_configured', 10),
                                 ('fail', 'helper_failed', 10), ('slow', 'helper_timeout', 0.5)]:
            with self.subTest(name=name):
                event = shuttle.validate_request(request('helper', name=name), self.root)
                event['expires'] = time.time() + life
                shuttle.serve(event, self.root, config, FakeSession(self.root / 'speech'))
                self.assertEqual(self.result()['error_code'], code)
                self.assertNotIn('key=1', json.dumps(self.result()))

    def test_sweep_removes_only_old_leftovers(self):
        old, new = self.box / 'old.json', self.box / 'new.json'
        old.write_text('{}'); new.write_text('{}')
        os.utime(old, (time.time() - 700, time.time() - 700))
        (self.box / 'sub').mkdir()
        shuttle.sweep(self.box)
        self.assertEqual(sorted(p.name for p in self.box.iterdir()), ['new.json', 'sub'])


class SocketRoundTripTests(unittest.TestCase):
    """The real inbox socket and caller functions, with the listener side served in a thread."""

    def run_listener(self, root, config, session, count):
        r, w = os.pipe()
        served = []
        def loop():
            with os.fdopen(r) as source:
                for event in events(source, root):
                    served.append(event)
                    if event.get('source') == shuttle.SOURCE:
                        shuttle.serve(event, root, config, session)
                    if len(served) == count:
                        return
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        for _ in range(200):
            if (Path(root) / 'work-end.sock').exists():
                break
            time.sleep(.01)
        return thread, w, served

    def test_two_quick_sounds_and_a_helper_round_trip(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            root = Path(d); (root / 'speech').mkdir()
            session = FakeSession(root / 'speech')
            config = {'shuttle': {'helpers': {'echo': [sys.executable, '-c',
                      'import json,sys; print(json.dumps(json.load(sys.stdin)))']}}}
            thread, w, served = self.run_listener(root, config, session, 3)
            tone = root / 'tone.wav'; tone.write_bytes(WAV)
            try:
                first = shuttle.cast_wav(tone, state_dir=root, timeout=5)
                second = shuttle.cast_wav(tone, state_dir=root, timeout=5)  # No 15-second window.
                helper = shuttle.run_helper('echo', {'action': 'resolve'}, state_dir=root, timeout=5)
            finally:
                os.close(w)
                thread.join(5)
            self.assertTrue(first['finished'] and second['finished'])
            self.assertEqual(helper['output'], {'action': 'resolve'})
            self.assertEqual(len(session.played), 2)
            self.assertEqual([e['intent'] for e in served], ['cast_wav', 'cast_wav', 'helper'])
            self.assertEqual(list(shuttle.inbox(root).iterdir()), [])
            self.assertTrue(tone.exists())  # The caller's own file is only copied.

    def test_listener_error_reaches_caller_as_its_code(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            root = Path(d); (root / 'speech').mkdir()
            thread, w, _ = self.run_listener(root, {}, FakeSession(root / 'speech', fail='existing_media_preserved'), 1)
            tone = root / 'tone.wav'; tone.write_bytes(WAV)
            try:
                with self.assertRaisesRegex(RuntimeError, '^existing_media_preserved$'):
                    shuttle.cast_wav(tone, state_dir=root, timeout=5)
            finally:
                os.close(w)
                thread.join(5)

    def test_missing_listener_and_rejection_leave_no_audio(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            root = Path(d)
            tone = root / 'tone.wav'; tone.write_bytes(WAV)
            with self.assertRaisesRegex(shuttle.ShuttleError, 'shuttle_unavailable'):
                shuttle.cast_wav(tone, state_dir=root, timeout=2)
            self.assertEqual(list(shuttle.inbox(root).iterdir()), [])
            thread, w, _ = self.run_listener(root, {}, FakeSession(root), 1)
            tone.write_bytes(b'tiny')  # Below the WAV header size: the inbox refuses it.
            try:
                with self.assertRaisesRegex(shuttle.ShuttleError, 'shuttle_rejected'):
                    shuttle.cast_wav(tone, state_dir=root, timeout=2)
            finally:
                os.close(w)
                thread.join(2)
            self.assertEqual(list(shuttle.inbox(root).iterdir()), [])

    def test_google_home_requests_keep_their_freshness_and_duplicate_rules(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as d:
            root = Path(d)
            thread, w, served = self.run_listener(root, {}, FakeSession(root), 99)
            def send(payload):
                import socket
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2); client.connect(str(root / 'work-end.sock'))
                    client.sendall((json.dumps(payload) + '\n').encode())
                    return client.recv(64)
            try:
                self.assertEqual(send({'intent': 'work_end', 'created_at': time.time()}), b'queued\n')
                self.assertEqual(send({'intent': 'work_end', 'created_at': time.time()}), b'rejected\n')
                self.assertEqual(send({'source': shuttle.SOURCE, 'intent': 'helper', 'name': 'x'}), b'rejected\n')
            finally:
                os.close(w)
                thread.join(2)


class ListenerTests(unittest.TestCase):
    def test_shuttle_event_uses_listener_cast_session_and_leaves_conversation_alone(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            config = {'cast_name': 'fixture', 'model': 'fixture', 'whisper_cli': 'fixture',
                      'shuttle': {'helpers': {}}}
            (root / 'config.json').write_text(json.dumps(config))
            event = {'source': shuttle.SOURCE, 'intent': 'cast_wav', 'id': KEY,
                     'expires': time.time() + 10, 'wav': str(root / 'x.wav')}
            stack.enter_context(patch.object(sys, 'argv', ['listener', '--state-dir', str(root)]))
            stack.enter_context(patch.object(sys, 'stdin', io.StringIO(json.dumps(event) + '\n')))
            stack.enter_context(patch.object(sys, 'stdout', io.StringIO()))
            stack.enter_context(patch.object(app.signal, 'signal'))
            stack.enter_context(patch.object(app.time, 'sleep'))
            stt = stack.enter_context(patch.object(app, 'JSONWorker')).return_value
            cast = stack.enter_context(patch.object(app, 'CastSession')).return_value
            cast.startup_prepare_s = 0; cast.startup_prepare_error = None
            speech = stack.enter_context(patch.object(app, 'SpeechQueue'))
            qa = stack.enter_context(patch.object(app, 'CursorQA'))
            served = stack.enter_context(patch.object(app.jarvis_shuttle, 'serve'))
            app.main()
            served.assert_called_once()
            args = served.call_args.args
            self.assertEqual((args[0], args[1], args[3]), (event, root, cast))
            self.assertEqual(args[2]['shuttle'], {'helpers': {}})
            stt.send.assert_not_called(); speech.assert_not_called(); qa.assert_not_called()
            self.assertFalse((root / 'last-turn.json').exists())
            self.assertFalse((root / 'last-wake.json').exists())


class InstallerTests(unittest.TestCase):
    def test_installer_copies_the_shuttle_and_keeps_its_configuration(self):
        source = (Path(__file__).resolve().parents[1] / 'install-jarvis-mac-listener.py').read_text()
        self.assertIn("'jarvis_shuttle.py'", source)
        self.assertIn("if 'shuttle' in existing: config['shuttle'] = existing['shuttle']", source)


if __name__ == '__main__':
    unittest.main()
