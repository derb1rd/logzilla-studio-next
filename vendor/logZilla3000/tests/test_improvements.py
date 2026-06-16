"""
Регрессионные тесты для переработки ядра: устранение «плохого распарса».

Каждый класс закрывает конкретный воспроизведённый дефект:
- генерик-разбор текстовых логов (timestamp/level/thread/logger) без мусорных
  http_status/json_snippet из миллисекунд и фрагментов {...};
- logfmt (key=value) как первоклассный формат;
- klog/glog (компоненты Kubernetes);
- RFC5424 syslog (PRI→facility/severity, structured-data, NILVALUE);
- сохранение ведущих нулей при коэрции (zip/телефоны/ID);
- CSV с пробелами/единицами в заголовке + защита от «прозы с запятыми»;
- отсутствие катастрофического бэктрекинга на длинных строках.
"""

import time
import unittest

from logZilla3000.detectors import FormatDetector, LogFormat
from logZilla3000.converters import JSONConverter
from logZilla3000.parser import UniversalLogParser
from logZilla3000.text_parser import parse_generic_line, canon_level


class TestGenericTextParser(unittest.TestCase):
    """Структурный разбор «голой» текстовой строки лога."""

    def test_iso_app_log_no_false_http_status(self):
        """Миллисекунды НЕ должны попадать в http_status (главный дефект)."""
        line = "2023-01-02 03:04:05,123 INFO  [main] com.foo.Bar - started ok"
        rec = parse_generic_line(line)
        self.assertEqual(rec["timestamp"], "2023-01-02 03:04:05,123")
        self.assertEqual(rec["level"], "INFO")
        self.assertEqual(rec["thread"], "main")
        self.assertEqual(rec["logger"], "com.foo.Bar")
        self.assertEqual(rec["message"], "started ok")
        self.assertNotIn("http_status", rec)
        self.assertNotIn("json_snippet", rec)
        self.assertNotIn("raw", rec)

    def test_iso_t_separator_and_zulu(self):
        rec = parse_generic_line("2025-01-15T10:30:00.123Z ERROR boom happened")
        self.assertEqual(rec["level"], "ERROR")
        self.assertTrue(rec["timestamp"].startswith("2025-01-15T10:30:00.123"))
        self.assertEqual(rec["message"], "boom happened")

    def test_bracketed_level_only(self):
        rec = parse_generic_line("2025-01-15 10:30:00 [WARN] cache almost full")
        self.assertEqual(rec["level"], "WARN")
        self.assertEqual(rec["message"], "cache almost full")

    def test_unstructured_line_returns_none(self):
        self.assertIsNone(parse_generic_line("just some prose without structure"))
        self.assertIsNone(parse_generic_line("line1"))

    def test_bare_level_guard_rejects_prose(self):
        """Titlecase/слово-уровень без таймстампа не должно ложно ставить level."""
        self.assertIsNone(parse_generic_line("Information about the system is here"))
        self.assertIsNone(parse_generic_line("Started the service successfully"))

    def test_bare_level_accepted_when_caps_or_colon(self):
        self.assertEqual(parse_generic_line("ERROR something broke")["level"], "ERROR")
        self.assertEqual(parse_generic_line("Error: connection refused")["level"], "ERROR")

    def test_level_canonicalization(self):
        self.assertEqual(canon_level("warning"), "WARN")
        self.assertEqual(canon_level("ERR"), "ERROR")
        self.assertEqual(canon_level("critical"), "CRITICAL")
        self.assertEqual(canon_level("panic"), "FATAL")

    def test_parser_text_path_structured(self):
        data = (
            "2025-01-15 10:30:00 INFO  [main] Application started\n"
            "some random line without structure"
        )
        out = UniversalLogParser().parse(data)
        self.assertEqual(out[0]["level"], "INFO")
        self.assertEqual(out[0]["message"], "Application started")
        self.assertEqual(out[1], {"message": "some random line without structure"})


class TestKlog(unittest.TestCase):
    """klog/glog: Lmmdd hh:mm:ss.uuuuuu tid file:line] msg."""

    def test_klog_line(self):
        rec = parse_generic_line("E0102 03:04:05.123456  1234 server.go:99] failed to bind")
        self.assertEqual(rec["level"], "ERROR")
        self.assertEqual(rec["tid"], 1234)
        self.assertEqual(rec["source"], "server.go:99")
        self.assertEqual(rec["message"], "failed to bind")
        self.assertEqual(rec["timestamp"], "01-02 03:04:05.123456")


class TestLogfmt(unittest.TestCase):
    """logfmt key=value (Go/Grafana/Heroku/systemd)."""

    SAMPLE = (
        'level=info ts=2023-01-02T03:04:05Z msg="request done" status=200 dur=1.23\n'
        'level=error ts=2023-01-02T03:04:06Z msg="db fail" status=500'
    )

    def test_detect(self):
        self.assertEqual(FormatDetector().detect(self.SAMPLE), LogFormat.LOGFMT)

    def test_convert_types_and_quotes(self):
        out = UniversalLogParser().parse(self.SAMPLE)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["level"], "info")
        self.assertEqual(out[0]["msg"], "request done")
        self.assertEqual(out[0]["status"], 200)
        self.assertEqual(out[0]["dur"], 1.23)

    def test_quoted_value_unicode_and_escapes(self):
        """Кавычки/переводы строк снимаются, кириллица не ломается (не unicode_escape)."""
        out = UniversalLogParser().parse('msg="привет \\"мир\\"\\nдалее" k=1')
        self.assertEqual(out[0]["msg"], 'привет "мир"\nдалее')
        self.assertEqual(out[0]["k"], 1)

    def test_single_pair_is_not_logfmt(self):
        """Одинокое key=value в прозе не должно опознаваться как logfmt."""
        prose = "the value x=1 appears in this sentence about something\nand more text here"
        self.assertNotEqual(FormatDetector().detect(prose), LogFormat.LOGFMT)


class TestSyslogRFC5424(unittest.TestCase):
    def test_pri_and_fields(self):
        line = ("<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su 1234 "
                "ID47 - 'su root' failed for lonvick")
        out = UniversalLogParser().parse(line)
        rec = out[0]
        self.assertEqual(rec["facility"], 4)
        self.assertEqual(rec["severity"], 2)
        self.assertEqual(rec["host"], "mymachine.example.com")
        self.assertEqual(rec["app"], "su")
        self.assertEqual(rec["message"], "'su root' failed for lonvick")

    def test_structured_data_and_nilvalue(self):
        line = ('<165>1 2003-10-11T22:14:15.003Z host evntslog - ID47 '
                '[exampleSDID@32473 iut="3"] An application event')
        rec = UniversalLogParser().parse(line)[0]
        self.assertIsNone(rec["pid"])  # '-' → NILVALUE
        self.assertIn("structured_data", rec)
        self.assertEqual(rec["message"], "An application event")

    def test_bsd_still_works(self):
        line = "Jan 15 10:30:00 webserver sshd[1234]: Accepted publickey for admin"
        rec = UniversalLogParser().parse(line)[0]
        self.assertEqual(rec["host"], "webserver")
        self.assertEqual(rec["pid"], 1234)


class TestLeadingZeroCoercion(unittest.TestCase):
    """Ведущие нули — это идентификаторы, не числа."""

    def setUp(self):
        self.conv = JSONConverter()

    def test_leading_zero_stays_string(self):
        self.assertEqual(self.conv._coerce_type("007"), "007")
        self.assertEqual(self.conv._coerce_type("01234"), "01234")
        self.assertEqual(self.conv._coerce_type("00.5"), "00.5")

    def test_plain_numbers_still_coerced(self):
        self.assertEqual(self.conv._coerce_type("0"), 0)
        self.assertEqual(self.conv._coerce_type("42"), 42)
        self.assertEqual(self.conv._coerce_type("0.5"), 0.5)
        self.assertEqual(self.conv._coerce_type("3.14"), 3.14)
        self.assertEqual(self.conv._coerce_type("-17"), -17)

    def test_csv_preserves_zip(self):
        out = UniversalLogParser().parse("id,zip\n007,01234\n008,00010")
        self.assertEqual(out[0]["id"], "007")
        self.assertEqual(out[0]["zip"], "01234")


class TestCsvHeaderLoosening(unittest.TestCase):
    def test_spaces_in_header(self):
        data = ("Request Time,Status Code,Client IP\n"
                "2023-01-01 10:00:00,200,10.0.0.1\n"
                "2023-01-01 10:00:01,500,10.0.0.2")
        self.assertEqual(FormatDetector().detect(data), LogFormat.CSV)
        out = UniversalLogParser().parse(data)
        self.assertEqual(out[0]["status_code"], 200)
        self.assertEqual(out[0]["client_ip"], "10.0.0.1")

    def test_units_in_header(self):
        data = "Duration (ms),error %,p95/p99\n12.5,0.1,3\n14.0,0.2,4"
        self.assertEqual(FormatDetector().detect(data), LogFormat.CSV)

    def test_prose_with_commas_not_csv(self):
        prose = ("Hello, world, this is text\n"
                 "Another, line, here goes\n"
                 "Third, row, of words")
        self.assertNotEqual(FormatDetector().detect(prose), LogFormat.CSV)

    def test_single_data_row_csv(self):
        """CSV с пробелами в шапке и одной строкой данных всё ещё CSV."""
        data = "Request Time,Status Code,Client IP\n2023-01-01 10:00:00,200,10.0.0.1"
        self.assertEqual(FormatDetector().detect(data), LogFormat.CSV)

    def test_single_row_prose_not_csv(self):
        self.assertNotEqual(
            FormatDetector().detect("Hello there, my friend, how are you"),
            LogFormat.CSV,
        )

    def test_normal_csv_regression(self):
        data = ("timestamp,level,msg\n"
                "2025-01-01T00:00:00,INFO,ok\n"
                "2025-01-01T00:00:01,WARN,slow")
        self.assertEqual(FormatDetector().detect(data), LogFormat.CSV)


class TestDeepExpand(unittest.TestCase):
    """Рекурсивное «вскрытие структуры» — JSON, зарытый в строковых значениях."""

    def setUp(self):
        from logZilla3000.message_expander import deep_expand
        self.deep_expand = deep_expand

    def test_recovers_nested_json_string(self):
        rec = {"a": 1, "event_original": '{"level":"ERROR","inner":"{\\"k\\":1}"}'}
        out = self.deep_expand([rec])[0]
        self.assertEqual(out["event_original"]["level"], "ERROR")
        self.assertEqual(out["event_original"]["inner"], {"k": 1})  # раскрыто рекурсивно

    def test_plain_text_untouched(self):
        rec = {"msg": "hello world", "note": "см. {x} в шаблоне"}
        out = self.deep_expand([rec])[0]
        self.assertEqual(out["msg"], "hello world")
        self.assertEqual(out["note"], "см. {x} в шаблоне")  # не целиком JSON → не трогаем

    def test_unicode_escape_decoded(self):
        rec = {"p": '{"msg":"\\u041e\\u0448\\u0438\\u0431\\u043a\\u0430"}'}
        out = self.deep_expand([rec])[0]
        self.assertEqual(out["p"]["msg"], "Ошибка")

    def test_disabled_is_noop(self):
        rec = {"x": '{"a":1}'}
        self.assertEqual(self.deep_expand([rec], enabled=False)[0]["x"], '{"a":1}')

    def test_via_parser_csv_json_column(self):
        # CSV-колонка с JSON-строкой → рекурсивно вскрывается в объект.
        from logZilla3000.parser import UniversalLogParser
        data = 'ts,payload\n2026-01-01,"{""level"":""WARN"",""rows"":5}"'
        out = UniversalLogParser().parse(data)[0]
        # expand_json_columns поднимает поля объекта наверх → level/rows как колонки
        self.assertEqual(out.get("level"), "WARN")
        self.assertEqual(out.get("rows"), 5)


class TestGroupInfra(unittest.TestCase):
    """Инфраструктурные поля сворачиваются в _meta, лог сервиса — наверху."""

    def setUp(self):
        from logZilla3000.message_expander import group_infra_fields
        self.group = group_infra_fields

    def test_infra_moved_to_meta(self):
        rec = {
            "timestamp": "t", "level": "ERROR", "msg": "boom", "caller": "api/main.go:1",
            "kubernetes_pod_name": "p", "kubernetes_labels_app_name": "a",
            "container_image": "img", "docker_id": "d", "namespace": "ns",
            "event_original": {"x": 1},
        }
        out = self.group([rec])[0]
        # Прикладные поля — наверху
        for k in ("timestamp", "level", "msg", "caller"):
            self.assertIn(k, out)
        # Инфраструктура — в _meta, не наверху
        for k in ("kubernetes_pod_name", "kubernetes_labels_app_name",
                  "container_image", "docker_id", "namespace", "event_original"):
            self.assertNotIn(k, out)
            self.assertIn(k, out["_meta"])

    def test_app_id_type_not_demoted(self):
        """id/type/index/score — частые прикладные имена, их НЕ трогаем."""
        rec = {"level": "INFO", "id": "req-1", "type": "order", "msg": "ok"}
        out = self.group([rec])[0]
        self.assertEqual(out["id"], "req-1")
        self.assertEqual(out["type"], "order")
        self.assertNotIn("_meta", out)  # инфра-полей нет → no-op

    def test_no_infra_is_noop(self):
        rec = {"timestamp": "t", "level": "INFO", "msg": "hi"}
        self.assertEqual(self.group([rec])[0], rec)


class TestPerformance(unittest.TestCase):
    def test_long_line_no_backtracking(self):
        """Длинная строка без '=' не должна вызывать катастрофический бэктрекинг."""
        start = time.perf_counter()
        UniversalLogParser().parse("A" * 200000)
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_long_line_with_equals(self):
        start = time.perf_counter()
        UniversalLogParser().parse("A" * 100000 + "=value")
        self.assertLess(time.perf_counter() - start, 1.0)


if __name__ == "__main__":
    unittest.main()
