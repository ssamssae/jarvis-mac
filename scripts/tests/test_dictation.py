import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_dictation import Dictation, deliver


class DictationTest(unittest.TestCase):
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
