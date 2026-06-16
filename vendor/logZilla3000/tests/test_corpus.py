"""
Data-driven проверка корпуса реальных вариантов логов (corpus.CASES).

Гарантирует, что подавляющее большинство встречающихся в проде форматов
распознаётся и разбирается правильно: верный формат + ключевые поля + отсутствие
мусорных полей. Каждый кейс — отдельный subTest, поэтому в отчёте видно ровно,
какой вариант сломался.
"""

import unittest

from logZilla3000.detectors import FormatDetector, LogFormat
from logZilla3000.parser import UniversalLogParser
from logZilla3000.tests.corpus import CASES


def _first_record(result):
    """Первая запись результата (list — первый элемент; dict с секциями — первая
    непустая секция-список; иначе сам dict)."""
    if isinstance(result, list):
        return result[0] if result else {}
    if isinstance(result, dict):
        for value in result.values():
            if isinstance(value, list) and value:
                return value[0]
        return result
    return {}


class TestCorpus(unittest.TestCase):
    def test_all_cases(self):
        detector = FormatDetector()
        for case in CASES:
            with self.subTest(case=case["name"]):
                fmt = detector.detect(case["raw"]).value
                result = UniversalLogParser().parse(case["raw"])
                rec = _first_record(result)

                if case.get("fmt"):
                    self.assertEqual(
                        fmt, case["fmt"],
                        f"{case['name']}: формат {fmt} ≠ {case['fmt']}",
                    )
                for key, expected in case.get("checks", {}).items():
                    self.assertEqual(
                        rec.get(key), expected,
                        f"{case['name']}: поле {key}={rec.get(key)!r} ≠ {expected!r}",
                    )
                for key in case.get("present", []):
                    self.assertIn(key, rec, f"{case['name']}: нет поля {key}")
                for key in case.get("absent", []):
                    self.assertNotIn(key, rec, f"{case['name']}: лишнее поле {key}")
                if case.get("count") and isinstance(result, list):
                    self.assertEqual(
                        len(result), case["count"],
                        f"{case['name']}: записей {len(result)} ≠ {case['count']}",
                    )

    def test_corpus_is_substantial(self):
        """Страховка: корпус не должен случайно «усохнуть» при рефакторинге."""
        self.assertGreaterEqual(len(CASES), 30)


if __name__ == "__main__":
    unittest.main()
