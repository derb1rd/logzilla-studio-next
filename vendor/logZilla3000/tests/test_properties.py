"""
Property/fuzz-тесты ядра: инварианты, которые должны держаться на ЛЮБОМ входе.

Это страховка «раз и навсегда»: вместо проверки конкретных форматов фиксируем
свойства движка (не падает, выдаёт валидный JSON, детерминирован), чтобы будущие
правки не сломали устойчивость на произвольных/битых данных.
"""

import json
import math
import random
import string
import unittest

from logZilla3000.parser import UniversalLogParser


def _walk(obj):
    """Все скалярные значения структуры (для проверки финитности чисел)."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


# Набор «зловредных»/пограничных входов.
_FUZZ_SEEDS = [
    "", " ", "\n\n\n", "\x00\x00", "﻿", "{", "[", '{"a":', "][",
    "a,b,c\n1,2", "level=", "=", "<34>1", "I0102", "\x1b[31m",
    '{"x": 1e999}', '{"x": NaN}', "1e999", "007", "-", "null",
    "a" * 5000, "\t".join(["c"] * 50), "ключ=значение текст",
    '{"a":"\\ud83d"}', "Jun 99 99:99:99 host app: msg",
]


class TestParserProperties(unittest.TestCase):
    def setUp(self):
        self.parser = UniversalLogParser()

    def test_never_crashes_on_seeds(self):
        for data in _FUZZ_SEEDS:
            with self.subTest(data=data[:30]):
                self.parser.parse(data)  # не должно бросать

    def test_never_crashes_on_random(self):
        rng = random.Random(42)
        alphabet = string.printable + "{}[]\",:;=\tкириллица\x00\x1b"
        for _ in range(200):
            n = rng.randint(0, 400)
            data = "".join(rng.choice(alphabet) for _ in range(n))
            self.parser.parse(data)

    def test_output_is_serializable_strict_json(self):
        """Выход всегда сериализуется в СТРОГИЙ JSON: без Infinity/NaN
        (браузерный JSON.parse их не принимает)."""
        for data in _FUZZ_SEEDS + ['{"x": 1e999, "y": -1e999}', "huge,n\n1e999,2"]:
            with self.subTest(data=data[:30]):
                result = self.parser.parse(data)
                s = json.dumps(result, ensure_ascii=True, allow_nan=False)  # бросит на nan/inf
                self.assertIsInstance(s, str)
                for v in _walk(result):
                    if isinstance(v, float):
                        self.assertTrue(math.isfinite(v))

    def test_list_results_are_dicts(self):
        for data in ["a,b\n1,2", "level=info x=1\nlevel=warn x=2",
                     "2026-01-01 INFO ok", '{"a":1}\n{"b":2}']:
            with self.subTest(data=data[:30]):
                result = self.parser.parse(data)
                if isinstance(result, list):
                    self.assertTrue(all(isinstance(r, dict) for r in result))

    def test_deterministic(self):
        for data in _FUZZ_SEEDS:
            with self.subTest(data=data[:30]):
                a = json.dumps(self.parser.parse(data), ensure_ascii=True, default=str)
                b = json.dumps(self.parser.parse(data), ensure_ascii=True, default=str)
                self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
