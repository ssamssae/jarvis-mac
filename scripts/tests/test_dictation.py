import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_dictation import Dictation, deliver


class DictationTest(unittest.TestCase):
    def test_guided_selection_all_targets(self):
        for engine, expected_engine in [('코덱스', 'codex'), ('커서', 'cursor'), ('그록', 'grok')]:
            for node, expected_node in [('헤르메스', 'macbook14'), ('노트북', 'macbook14'), ('아테나', 'mac'), ('볼칸', 'macmini')]:
                with self.subTest(engine=engine, node=node):
                    d = Dictation()
                    self.assertEqual(d.select('음성 입력'), '어디로 연결할까요?')
                    self.assertIsNone(d.target)
                    self.assertEqual(d.select(engine), '어떤 노드인가요?')
                    self.assertEqual(d.select(node), '말씀하세요.')
                    self.assertFalse(d.selecting)
                    self.assertEqual(d.target, (expected_node, expected_engine))
                    self.assertEqual(d.accept('원문 CASE 엔터')[1]['text'], '원문 CASE')

    def test_selection_reprompts_and_cancel(self):
        d = Dictation()
        self.assertIsNone(d.select('음성 입력 방법 알려줘'))
        d.select('음성입력')
        self.assertIn('코덱스', d.select('선풍기 꺼줘'))
        self.assertEqual(d.selection_stage, 'engine')
        d.select('Cursor')
        self.assertIn('노트북', d.select('페르멘스'))
        self.assertIsNone(d.target)
        self.assertIn('취소', d.select('취소', cancelled=True))
        self.assertFalse(d.selecting)
        self.assertIsNone(d.selection_engine)

    def test_selection_expiry_and_restart_do_not_reuse_engine(self):
        d = Dictation(); d.select('음성 입력'); d.select('코덱스')
        d.selection_until = 0
        self.assertIn('시간', d.select('노트북'))
        self.assertIsNone(d.target)
        self.assertFalse(d.selecting)
        d.select('음성 입력'); d.select('커서'); d.select('음성 입력')
        self.assertEqual(d.selection_stage, 'engine')
        self.assertIsNone(d.selection_engine)

    def test_all_targets_and_alias(self):
        for node in ('헤르메스','아테나','볼칸','볼탄'):
            for engine in ('코덱스','그록','커서'):
                d = Dictation()
                self.assertTrue(d.start(node + ' ' + engine))
                self.assertEqual(d.accept('첫 문장입니다')[0], 'collecting')
                self.assertEqual(d.accept('')[0], 'collecting')
                self.assertEqual(d.accept('두 번째 문장입니다')[0], 'collecting')
                action, request = d.accept('마지막 문장입니다 엔터')
                self.assertEqual(action, 'submit')
                self.assertEqual(request['text'], '첫 문장입니다 두 번째 문장입니다 마지막 문장입니다')
                self.assertIsNone(d.target)

    def test_enter_in_sentence_is_content(self):
        d = Dictation(); d.start('헤르메스 코덱스')
        self.assertEqual(d.accept('엔터 키의 동작을 설명해줘')[0], 'collecting')
        self.assertEqual(d.accept('엔터')[1]['text'], '엔터 키의 동작을 설명해줘')

    def test_empty_enter_and_arbitrary_text(self):
        d = Dictation(); d.start('아테나 커서')
        self.assertEqual(d.accept('엔터'), ('empty', None))
        self.assertIsNotNone(d.target)
        self.assertEqual(d.accept('승인 엔터')[1]['text'], '승인')
        d.start('헤르메스 코덱스')
        self.assertEqual(d.accept('취소 엔터')[1]['text'], '취소')

    def test_start_is_exact(self):
        for text in ('아테나', '헤르메스 코덱스가 뭐야', '다른 헤르메스 코덱스', '커서'):
            self.assertFalse(Dictation().start(text))

    def test_observed_hermes_alias_routes_without_rewriting_content(self):
        for engine, expected in (('코덱스', 'codex'), ('그록', 'grok'), ('커서', 'cursor')):
            for text in ('헬멧스 ' + engine, '헬멧스' + engine + '.'):
                with self.subTest(text=text):
                    d = Dictation()
                    self.assertTrue(d.start(text))
                    self.assertEqual(d.target, ('macbook14', expected))
                    action, request = d.accept('헬멧스라는 단어를 설명해줘 엔터')
                    self.assertEqual(action, 'submit')
                    self.assertEqual((request['node'], request['engine']), ('macbook14', expected))
                    self.assertEqual(request['text'], '헬멧스라는 단어를 설명해줘')

    def test_hermes_alias_does_not_capture_questions_or_near_matches(self):
        for text in ('헬멧스', '헬멧스 코덱스가 뭐야', '다른 헬멧스 코덱스',
                     '헬멧스 코덱스 선풍기 꺼줘', '헬멧 코덱스', '헬멧스코덱스입니다'):
            with self.subTest(text=text):
                d = Dictation()
                self.assertFalse(d.start(text))
                self.assertIsNone(d.target)

    def test_english_and_mixed_hermes_invocations(self):
        for text in ('Hermes Codex', 'hermes codex', 'HERMES CODEX',
                     'Hermes 코덱스', '헤르메스 Codex', '헬멧스 CODEX',
                     ' Hermes Codex! ', 'HermesCodex'):
            with self.subTest(text=text):
                d = Dictation()
                self.assertTrue(d.start(text))
                self.assertEqual(d.target, ('macbook14', 'codex'))
                action, request = d.accept('Keep Hermes Codex CASE 엔터')
                self.assertEqual(action, 'submit')
                self.assertEqual(request['text'], 'Keep Hermes Codex CASE')

    def test_english_invocation_boundary(self):
        for text in ('What is Hermes Codex', 'Hermes Codex가 뭐야',
                     'Hermes Codex turn off fan', 'Hermes Codexes',
                     'Hermes', 'Codex', 'HermesX Codex'):
            with self.subTest(text=text):
                self.assertFalse(Dictation().start(text))

    def test_long_dictation_does_not_finish_at_segment_boundary(self):
        d = Dictation(); d.start('볼칸 그록')
        for _ in range(100):
            self.assertEqual(d.accept('긴 이야기의 다음 문장입니다')[0], 'collecting')
        self.assertEqual(d.accept('엔터')[1]['text'].count('다음'), 100)

    def test_no_retry_on_ambiguous_transport(self):
        runner = Mock(side_effect=TimeoutError())
        self.assertEqual(deliver({'argv':['receiver']}, {'id':'test'}, runner)['status'], 'unknown')
        self.assertEqual(runner.call_count, 1)

    def test_receipt_identity_must_match(self):
        runner = Mock(return_value=Mock(returncode=0, stdout='{"id":"other","status":"submitted"}'))
        self.assertEqual(deliver({'argv':['receiver']}, {'id':'test'}, runner)['status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
