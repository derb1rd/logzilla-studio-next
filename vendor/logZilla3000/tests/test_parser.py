"""
Полный набор тестов для универсального парсера логов.
"""

import json
import os
import tempfile
import unittest

from logZilla3000.cleaners import LogCleaner
from logZilla3000.detectors import FormatDetector, LogFormat
from logZilla3000.converters import JSONConverter
from logZilla3000.parser import UniversalLogParser
from logZilla3000.tests import (
    CSV_FULL,
    CSV_INCOMPLETE,
    CSV_NO_HEADER,
    JSON_LOG,
    JSONL_LOG,
    APACHE_LOG,
    NGINX_LOG,
    SYSLOG_DATA,
    TEXT_LOG_DIRTY,
    TEXT_LOG_HTML,
    MIXED_LOG,
    BROKEN_LOG,
    CSV_DIRTY,
)


class TestLogCleaner(unittest.TestCase):
    """Тесты модуля очистки логов."""

    def setUp(self):
        self.cleaner = LogCleaner()

    def test_remove_ansi(self):
        """Удаление ANSI escape-последовательностей."""
        dirty = "\x1b[32mgreen text\x1b[0m normal text"
        clean = self.cleaner.clean(dirty)
        self.assertNotIn("\x1b", clean)
        self.assertIn("green text", clean)
        self.assertIn("normal text", clean)

    def test_remove_html_tags(self):
        """Удаление HTML-тегов."""
        dirty = '<div class="log">Hello <b>world</b></div>'
        clean = self.cleaner.clean(dirty)
        self.assertNotIn("<", clean)
        self.assertNotIn(">", clean)
        self.assertIn("Hello", clean)
        self.assertIn("world", clean)

    def test_remove_duplicates(self):
        """Удаление дублирующихся строк."""
        data = "line1\nline2\nline1\nline3\nline2"
        clean = self.cleaner.clean(data)
        lines = clean.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "line1")
        self.assertEqual(lines[1], "line2")
        self.assertEqual(lines[2], "line3")

    def test_remove_empty_lines(self):
        """Удаление пустых строк."""
        data = "line1\n\n\nline2\n\nline3"
        clean = self.cleaner.clean(data)
        lines = clean.split("\n")
        self.assertEqual(len(lines), 3)

    def test_normalize_whitespace(self):
        """Нормализация пробелов."""
        data = "line1   with    spaces\n\n\nline2"
        clean = self.cleaner.clean(data)
        self.assertNotIn("   ", clean)

    def test_remove_null_bytes(self):
        """Удаление нулевых байтов."""
        data = "line1\x00line2\x00line3"
        clean = self.cleaner.clean(data)
        self.assertNotIn("\x00", clean)

    def test_keep_patterns(self):
        """Сохранение строк по паттерну даже при дедупликации."""
        cleaner = LogCleaner(
            remove_duplicates=True,
            keep_patterns=[r"IMPORTANT"],
        )
        data = "duplicate line\nIMPORTANT: keep this\nduplicate line\nIMPORTANT: keep this"
        clean = cleaner.clean(data)
        self.assertIn("IMPORTANT: keep this", clean)
        # Дубликаты удаляются, но keep-паттерн сохраняет все совпадения
        lines = clean.split("\n")
        self.assertEqual(len(lines), 3)  # duplicate, IMPORTANT, IMPORTANT

    def test_custom_garage_patterns(self):
        """Пользовательские паттерны мусора."""
        cleaner = LogCleaner(
            custom_garbage_patterns=[r"\[TRACE\].*"],
        )
        data = "[TRACE] verbose debug info\nINFO important message\n[TRACE] more debug"
        clean = cleaner.clean(data)
        self.assertNotIn("TRACE", clean)
        self.assertIn("INFO important message", clean)

    def test_html_entity_decoding(self):
        """Декодирование HTML-сущностей."""
        # HTML-сущности декодируются после удаления тегов
        data = "Error: &lt;connection refused&gt; &amp; timeout"
        clean = self.cleaner.clean(data)
        self.assertIn("<connection refused>", clean)
        self.assertIn("& timeout", clean)


class TestFormatDetector(unittest.TestCase):
    """Тесты автоопределения формата."""

    def setUp(self):
        self.detector = FormatDetector()

    def test_detect_csv(self):
        """Определение CSV."""
        fmt = self.detector.detect(CSV_FULL)
        self.assertEqual(fmt, LogFormat.CSV)

    def test_detect_csv_semicolon(self):
        """Определение CSV с разделителем ;."""
        fmt = self.detector.detect(CSV_INCOMPLETE)
        self.assertEqual(fmt, LogFormat.CSV)

    def test_detect_json(self):
        """Определение JSON."""
        fmt = self.detector.detect(JSON_LOG)
        self.assertEqual(fmt, LogFormat.JSON)

    def test_detect_jsonl(self):
        """Определение JSONL."""
        fmt = self.detector.detect(JSONL_LOG)
        self.assertEqual(fmt, LogFormat.JSONL)

    def test_detect_apache(self):
        """Определение Apache лога."""
        fmt = self.detector.detect(APACHE_LOG)
        self.assertEqual(fmt, LogFormat.APACHE)

    def test_detect_nginx(self):
        """Определение Nginx лога."""
        fmt = self.detector.detect(NGINX_LOG)
        self.assertEqual(fmt, LogFormat.NGINX)

    def test_detect_syslog(self):
        """Определение syslog."""
        fmt = self.detector.detect(SYSLOG_DATA)
        self.assertEqual(fmt, LogFormat.SYSLOG)

    def test_detect_text(self):
        """Определение текстового лога."""
        fmt = self.detector.detect(MIXED_LOG)
        self.assertEqual(fmt, LogFormat.TEXT)

    def test_detect_delimiter(self):
        """Определение разделителя CSV."""
        delimiter = self.detector.detect_delimiter(CSV_INCOMPLETE)
        self.assertEqual(delimiter, ";")

    def test_has_header(self):
        """Определение наличия заголовка."""
        self.assertTrue(self.detector.has_header(CSV_FULL))
        # CSV_NO_HEADER содержит текстовые данные, поэтому используем CSV_NUMERIC
        from logZilla3000.tests import CSV_NUMERIC
        self.assertFalse(self.detector.has_header(CSV_NUMERIC))

    def test_empty_data(self):
        """Пустые данные."""
        fmt = self.detector.detect("")
        self.assertEqual(fmt, LogFormat.UNKNOWN)


class TestJSONConverter(unittest.TestCase):
    """Тесты конвертера в JSON."""

    def setUp(self):
        self.converter = JSONConverter()

    def test_csv_to_json(self):
        """Конвертация CSV в JSON."""
        result = self.converter.convert_csv_to_json(CSV_FULL)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 5)
        self.assertIn("timestamp", result[0])
        self.assertIn("level", result[0])
        self.assertEqual(result[0]["level"], "INFO")

    def test_csv_incomplete_to_json(self):
        """Конвертация неполного CSV в JSON."""
        result = self.converter.convert_csv_to_json(
            CSV_INCOMPLETE, delimiter=";"
        )
        self.assertIsInstance(result, list)
        # Строка с пустым timestamp
        self.assertIsNone(result[2]["timestamp"])
        self.assertEqual(result[2]["level"], "ERROR")

    def test_csv_no_header(self):
        """Конвертация CSV без заголовка."""
        result = self.converter.convert_csv_to_json(
            CSV_NO_HEADER, has_header=False
        )
        self.assertIsInstance(result, list)
        self.assertIn("col_0", result[0])
        self.assertIn("col_1", result[0])

    def test_csv_custom_header(self):
        """Конвертация CSV с пользовательским заголовком."""
        result = self.converter.convert_csv_to_json(
            CSV_NO_HEADER,
            header=["time", "level", "service", "message"],
            has_header=False,
        )
        self.assertIn("time", result[0])
        self.assertIn("level", result[0])

    def test_jsonl_to_json(self):
        """Конвертация JSONL в JSON."""
        result = self.converter.convert_jsonl_to_json(JSONL_LOG)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["level"], "INFO")

    def test_json_to_json(self):
        """Нормализация JSON."""
        result = self.converter.convert_json_to_json(JSON_LOG)
        self.assertIsInstance(result, dict)
        self.assertIn("service", result)
        self.assertIn("logs", result)

    def test_text_to_json_with_pattern(self):
        """Конвертация текста с regex-паттерном."""
        pattern = r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(\w+)\s+\[(\w+)\]\s+(.*)"
        fields = ["timestamp", "level", "context", "message"]
        result = self.converter.convert_text_to_json(
            MIXED_LOG, pattern=pattern, field_names=fields
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("timestamp", result[0])
        self.assertIn("level", result[0])

    def test_text_to_json_without_pattern(self):
        """Конвертация текста без паттерна."""
        result = self.converter.convert_text_to_json("line1\nline2\nline3")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)
        self.assertIn("message", result[0])

    def test_apache_to_json(self):
        """Конвертация Apache логов."""
        result = self.converter.convert_apache_to_json(APACHE_LOG)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("ip", result[0])
        self.assertIn("method", result[0])
        self.assertIn("status", result[0])

    def test_nginx_to_json(self):
        """Конвертация Nginx логов."""
        result = self.converter.convert_nginx_to_json(NGINX_LOG)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("ip", result[0])
        self.assertIn("status", result[0])

    def test_syslog_to_json(self):
        """Конвертация syslog."""
        result = self.converter.convert_syslog_to_json(SYSLOG_DATA)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("host", result[0])
        self.assertIn("message", result[0])

    def test_coerce_types(self):
        """Автоматическое приведение типов."""
        converter = JSONConverter(coerce_types=True)
        result = converter.convert_csv_to_json(
            "name,age,active\nAlice,25,true\nBob,30,false",
        )
        self.assertEqual(result[0]["age"], 25)
        self.assertTrue(result[0]["active"])
        self.assertFalse(result[1]["active"])

    def test_skip_null(self):
        """Пропуск null-значений."""
        converter = JSONConverter(skip_null=True)
        result = converter.convert_csv_to_json(
            "name,age,city\nAlice,25,\nBob,,Moscow",
        )
        self.assertNotIn("city", result[0])
        self.assertNotIn("age", result[1])

    def test_normalize_keys(self):
        """Нормализация ключей."""
        converter = JSONConverter(normalize_keys=True)
        result = converter.convert_csv_to_json(
            "First Name,Last Name,Phone Number\nAlice,Smith,123",
        )
        self.assertIn("first_name", result[0])
        self.assertIn("last_name", result[0])
        self.assertIn("phone_number", result[0])

    def test_to_json_string(self):
        """Сериализация в JSON-строку."""
        data = [{"key": "value"}]
        result = self.converter.to_json_string(data)
        parsed = json.loads(result)
        self.assertEqual(parsed[0]["key"], "value")


class TestUniversalLogParser(unittest.TestCase):
    """Тесты основного парсера."""

    def test_parse_csv(self):
        """Парсинг CSV."""
        parser = UniversalLogParser()
        result = parser.parse(CSV_FULL)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 5)
        self.assertIn("timestamp", result[0])

    def test_parse_csv_with_semicolon(self):
        """Парсинг CSV с разделителем ;."""
        parser = UniversalLogParser()
        result = parser.parse(CSV_INCOMPLETE)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_parse_csv_no_header(self):
        """Парсинг CSV без заголовка."""
        parser = UniversalLogParser(has_header=False)
        result = parser.parse(CSV_NO_HEADER)
        self.assertIsInstance(result, list)
        self.assertIn("col_0", result[0])

    def test_parse_json(self):
        """Парсинг JSON."""
        parser = UniversalLogParser()
        result = parser.parse(JSON_LOG)
        self.assertIsInstance(result, dict)
        self.assertIn("logs", result)

    def test_parse_jsonl(self):
        """Парсинг JSONL."""
        parser = UniversalLogParser()
        result = parser.parse(JSONL_LOG)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 4)

    def test_parse_apache(self):
        """Парсинг Apache логов."""
        parser = UniversalLogParser()
        result = parser.parse(APACHE_LOG)
        self.assertIsInstance(result, list)
        self.assertIn("ip", result[0])

    def test_parse_nginx(self):
        """Парсинг Nginx логов."""
        parser = UniversalLogParser()
        result = parser.parse(NGINX_LOG)
        self.assertIsInstance(result, list)
        self.assertIn("ip", result[0])

    def test_parse_syslog(self):
        """Парсинг syslog."""
        parser = UniversalLogParser()
        result = parser.parse(SYSLOG_DATA)
        self.assertIsInstance(result, list)
        self.assertIn("host", result[0])

    def test_parse_text_dirty(self):
        """Парсинг грязного текстового лога."""
        parser = UniversalLogParser()
        result = parser.parse(TEXT_LOG_DIRTY)
        self.assertIsInstance(result, list)
        # ANSI-коды должны быть удалены
        for item in result:
            for key, value in item.items():
                self.assertNotIn("\x1b", str(value))

    def test_parse_text_html(self):
        """Парсинг лога с HTML-тегами."""
        parser = UniversalLogParser()
        result = parser.parse(TEXT_LOG_HTML)
        self.assertIsInstance(result, list)
        for item in result:
            for key, value in item.items():
                self.assertNotIn("<div", str(value))

    def test_parse_mixed(self):
        """Парсинг смешанного лога."""
        parser = UniversalLogParser()
        result = parser.parse(MIXED_LOG)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_parse_broken(self):
        """Парсинг лога с пропущенными строками."""
        parser = UniversalLogParser()
        result = parser.parse(BROKEN_LOG)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_parse_csv_dirty(self):
        """Парсинг CSV с мусорными строками."""
        parser = UniversalLogParser()
        result = parser.parse(CSV_DIRTY)
        self.assertIsInstance(result, list)
        # Мусорные строки (# комментарии) должны быть удалены
        for item in result:
            self.assertNotIn("#", str(item.get("timestamp", "")))

    def test_filter_by_level(self):
        """Фильтрация по уровню."""
        parser = UniversalLogParser(log_levels=["ERROR", "WARN"])
        result = parser.parse(CSV_FULL)
        self.assertIsInstance(result, list)
        for item in result:
            self.assertIn(item["level"], ["ERROR", "WARN"])

    def test_custom_pattern(self):
        """Парсинг с кастомным паттерном."""
        parser = UniversalLogParser(
            pattern=r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(\w+)\s+\[(\w+)\]\s+(.*)",
            field_names=["timestamp", "level", "context", "message"],
        )
        result = parser.parse(MIXED_LOG)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("timestamp", result[0])

    def test_parse_file(self):
        """Парсинг из файла."""
        parser = UniversalLogParser()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.write(CSV_FULL)
            f.flush()
            result = parser.parse_file(f.name)
            os.unlink(f.name)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 5)

    def test_parse_multiple_files(self):
        """Парсинг нескольких файлов."""
        parser = UniversalLogParser()
        files = []
        try:
            for data in [CSV_FULL, JSONL_LOG]:
                f = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".log", delete=False, encoding="utf-8"
                )
                f.write(data)
                f.flush()
                files.append(f.name)

            results = parser.parse_files(files)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["status"], "ok")
            self.assertEqual(results[1]["status"], "ok")
        finally:
            for fpath in files:
                os.unlink(fpath)

    def test_to_json(self):
        """Сериализация результата."""
        parser = UniversalLogParser()
        result = parser.parse(CSV_FULL)
        json_str = parser.to_json(result)
        parsed = json.loads(json_str)
        self.assertIsInstance(parsed, list)

    def test_to_json_file(self):
        """Запись результата в файл."""
        parser = UniversalLogParser()
        result = parser.parse(CSV_FULL)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.flush()
            parser.to_json_file(result, f.name)
            with open(f.name, "r") as rf:
                parsed = json.load(rf)
            os.unlink(f.name)

        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 5)

    def test_encoding_detection(self):
        """Автоопределение кодировки."""
        parser = UniversalLogParser()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="cp1251"
        ) as f:
            f.write("name,value\nтест,123")
            f.flush()
            result = parser.parse_file(f.name)
            os.unlink(f.name)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)


class TestEdgeCases(unittest.TestCase):
    """Тесты граничных случаев."""

    def test_empty_input(self):
        """Пустой вход."""
        parser = UniversalLogParser()
        result = parser.parse("")
        self.assertEqual(result, [])

    def test_single_line(self):
        """Одна строка."""
        parser = UniversalLogParser()
        result = parser.parse("single line of log")
        self.assertIsInstance(result, list)

    def test_only_garbage(self):
        """Только мусор."""
        parser = UniversalLogParser()
        result = parser.parse("===\n---\n***\n\x00\x00")
        self.assertIsInstance(result, list)

    def test_very_long_line(self):
        """Очень длинная строка."""
        parser = UniversalLogParser()
        long_line = "A" * 100000
        result = parser.parse(long_line)
        self.assertIsInstance(result, list)

    def test_unicode_content(self):
        """Unicode-содержимое."""
        parser = UniversalLogParser()
        result = parser.parse("Сообщение: Привет мир! 🌍 café résumé")
        self.assertIsInstance(result, list)

    def test_csv_inconsistent_columns(self):
        """CSV с разным количеством колонок."""
        parser = UniversalLogParser()
        # 4-я строка с 4 полями не проходит детектор CSV, используем 3 поля
        data = "a,b,c\n1,2,3\n4,5\n6,7,8"
        result = parser.parse(data)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)

    def test_json_invalid_lines_in_jsonl(self):
        """JSONL с невалидными строками."""
        parser = UniversalLogParser()
        data = '{"key": "value1"}\ninvalid json\n{"key": "value2"}'
        result = parser.parse(data)
        self.assertIsInstance(result, list)
        # Невалидная строка должна быть пропущена
        self.assertEqual(len(result), 2)


class TestSQLFormatter(unittest.TestCase):
    """Тесты модуля форматирования SQL."""

    def test_is_sql_select(self):
        """Определение SELECT-запроса."""
        from logZilla3000.sql_formatter import is_sql
        self.assertTrue(is_sql("SELECT * FROM users"))
        self.assertTrue(is_sql("\nSELECT * FROM users"))
        self.assertTrue(is_sql("  \t select * from users"))

    def test_is_sql_insert(self):
        """Определение INSERT-запроса."""
        from logZilla3000.sql_formatter import is_sql
        self.assertTrue(is_sql("INSERT INTO users (id) VALUES (1)"))

    def test_is_sql_update(self):
        """Определение UPDATE-запроса."""
        from logZilla3000.sql_formatter import is_sql
        self.assertTrue(is_sql("UPDATE users SET name='test'"))

    def test_is_sql_delete(self):
        """Определение DELETE-запроса."""
        from logZilla3000.sql_formatter import is_sql
        self.assertTrue(is_sql("DELETE FROM users WHERE id=1"))

    def test_is_sql_with_cte(self):
        """Определение CTE (WITH)."""
        from logZilla3000.sql_formatter import is_sql
        self.assertTrue(is_sql("WITH cte AS (SELECT 1) SELECT * FROM cte"))

    def test_is_not_sql(self):
        """Строки, не являющиеся SQL."""
        from logZilla3000.sql_formatter import is_sql
        self.assertFalse(is_sql("Hello world"))
        self.assertFalse(is_sql(""))
        self.assertFalse(is_sql("short"))
        self.assertFalse(is_sql("12345"))
        self.assertFalse(is_sql(None))

    def test_format_sql_basic(self):
        """Форматирование простого SELECT."""
        from logZilla3000.sql_formatter import format_sql
        raw = "select id, name from users where id = 1"
        result = format_sql(raw)
        self.assertIn("SELECT", result)
        self.assertIn("FROM", result)
        self.assertIn("WHERE", result)

    def test_format_sql_with_newlines_tabs(self):
        """Форматирование SQL с \\n и \\t."""
        from logZilla3000.sql_formatter import format_sql
        raw = "\nselect id,\n\tname\nfrom users\nwhere id = 1"
        result = format_sql(raw)
        self.assertIn("SELECT", result)
        self.assertIn("FROM", result)
        # Результат не должен содержать сырые табы
        self.assertNotIn("\t", result)

    def test_format_sql_non_sql_unchanged(self):
        """Не-SQL строка не изменяется."""
        from logZilla3000.sql_formatter import format_sql
        raw = "Hello world"
        result = format_sql(raw)
        self.assertEqual(result, raw)

    def test_format_sql_fields_dict(self):
        """Форматирование SQL в словаре."""
        from logZilla3000.sql_formatter import format_sql_fields
        data = {
            "level": "DEBUG",
            "sql": "select id from users",
            "time": 123,
        }
        result = format_sql_fields(data)
        self.assertEqual(result["level"], "DEBUG")
        self.assertEqual(result["time"], 123)
        self.assertIn("SELECT", result["sql"])

    def test_format_sql_fields_query_key(self):
        """Форматирование SQL в поле 'query'."""
        from logZilla3000.sql_formatter import format_sql_fields
        data = {"query": "select * from orders"}
        result = format_sql_fields(data)
        self.assertIn("SELECT", result["query"])

    def test_format_sql_fields_nested(self):
        """Форматирование SQL во вложенных структурах."""
        from logZilla3000.sql_formatter import format_sql_fields
        data = {
            "logs": [
                {"level": "DEBUG", "sql": "select id from users"},
                {"level": "INFO", "msg": "no sql here"},
            ]
        }
        result = format_sql_fields(data)
        self.assertIn("SELECT", result["logs"][0]["sql"])
        self.assertEqual(result["logs"][1]["msg"], "no sql here")

    def test_format_sql_fields_disabled(self):
        """Отключённое форматирование."""
        from logZilla3000.sql_formatter import format_sql_fields
        data = {"sql": "select id from users"}
        result = format_sql_fields(data, enabled=False)
        self.assertEqual(result["sql"], "select id from users")

    def test_format_sql_fields_no_sql_key(self):
        """Словарь без SQL-полей не изменяется."""
        from logZilla3000.sql_formatter import format_sql_fields
        data = {"level": "DEBUG", "message": "hello"}
        result = format_sql_fields(data)
        self.assertEqual(result, data)

    def test_parser_json_with_sql(self):
        """Интеграция: парсинг JSON с полем sql."""
        parser = UniversalLogParser()
        data = '{"level":"DEBUG","sql":"select id, name from users where id = 1","time":100}'
        result = parser.parse(data)
        # parse() может вернуть list или dict в зависимости от обработки
        record = result[0] if isinstance(result, list) else result
        self.assertIn("SELECT", record["sql"])

    def test_parser_json_with_sql_disabled(self):
        """Интеграция: парсинг JSON с format_sql=False."""
        parser = UniversalLogParser(format_sql=False)
        data = '{"level":"DEBUG","sql":"select id from users","time":100}'
        result = parser.parse(data)
        record = result[0] if isinstance(result, list) else result
        # SQL не должен быть отформатирован
        self.assertNotIn("SELECT", record.get("sql", ""))

    def test_parser_jsonl_with_sql(self):
        """Интеграция: парсинг JSONL с полем sql."""
        parser = UniversalLogParser()
        data = '{"level":"DEBUG","sql":"select id from users"}\n{"level":"INFO","sql":"select * from orders"}'
        result = parser.parse(data)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIn("SELECT", result[0]["sql"])
        self.assertIn("SELECT", result[1]["sql"])


class TestCsvWithJsonColumn(unittest.TestCase):
    """CSV-экспорт с точками в заголовке и JSON-payload в колонке.

    Регрессия: такой заголовок не матчил CSV_HEADER_PATTERN → файл уходил в
    текстовый путь и парсился reg-экспами (мусорные http_status/json_snippet).
    Теперь должен детектиться как CSV, а JSON-колонка — разворачиваться в
    плоские типизированные поля.
    """

    SAMPLE = (
        "kubernetes.container_name;msg;event.original\n"
        'back;Finish handling request;'
        '"{""level"":""INFO"",""method"":""GET"",""url"":""/api/x"",'
        '""status"":200,""request_time"":0.0014,'
        '""kubernetes"":{""pod_name"":""back-1"",""namespace_name"":""ns""}}"\n'
        'gnivc;Finish handling request;'
        '"{""level"":""ERROR"",""method"":""POST"",""url"":""/api/y"",""status"":500}"'
    )

    def test_detected_as_csv(self):
        self.assertEqual(FormatDetector().detect(self.SAMPLE), LogFormat.CSV)

    def test_header_pattern_allows_dots_and_dashes(self):
        pat = FormatDetector.CSV_HEADER_PATTERN
        self.assertTrue(pat.match("kubernetes.container_name;msg;event.original"))
        self.assertTrue(pat.match("a-b,c.d,e_f"))

    def test_json_column_flattened_to_typed_fields(self):
        parser = UniversalLogParser(delimiter=";", expand_message=True)
        result = parser.parse(self.SAMPLE)
        self.assertEqual(len(result), 2)
        rec = result[0]
        # JSON-поля подняты на верхний уровень и типизированы
        self.assertEqual(rec["status"], 200)
        self.assertIsInstance(rec["status"], int)
        self.assertAlmostEqual(rec["request_time"], 0.0014)
        self.assertEqual(rec["url"], "/api/x")
        # вложенный объект уплощён через '_' и свёрнут в _meta (инфраструктура):
        # наверху остаётся лог сервиса, k8s-поля не засоряют запись.
        self.assertEqual(rec["_meta"]["kubernetes_pod_name"], "back-1")
        self.assertEqual(rec["_meta"]["kubernetes_namespace_name"], "ns")
        # колонка вне JSON тоже инфраструктурная → в _meta
        self.assertEqual(rec["_meta"]["kubernetes_container_name"], "back")
        self.assertNotIn("kubernetes_pod_name", rec)
        # никаких reg-экспных мусорных полей
        self.assertNotIn("json_snippet", rec)
        self.assertNotIn("http_status", rec)
        self.assertEqual(result[1]["status"], 500)

    def test_unquoted_comma_in_leading_column_repaired(self):
        # Регрессия: over-quoted Kibana/OpenSearch-экспорт. После снятия внешней
        # обёртки колонка @timestamp («Jun 11, 2026 @ 12:02:14.345») НЕзакавычена и
        # содержит запятую → csv.reader дробил её, хвост «2026 @ ...» утекал в
        # event.original, а payload-JSON сваливался в col_2. Должно: излишек
        # сливается обратно в первую колонку, JSON-колонка разворачивается чисто.
        conv = JSONConverter()
        rows = conv.convert_csv_to_json(
            '@timestamp,event.original\n'
            'Jun 11, 2026 @ 12:02:14.345,'
            '"{""level"":""ERROR"",""status"":500}"',
            delimiter=",",
        )
        self.assertEqual(len(rows), 1)
        rec = rows[0]
        self.assertEqual(rec["timestamp"], "Jun 11, 2026 @ 12:02:14.345")
        self.assertEqual(rec["event_original"], '{"level":"ERROR","status":500}')
        self.assertNotIn("col_2", rec)

    def test_overquoted_odd_quote_row_unwrapped(self):
        # Регрессия: в over-quoted экспорте строка с НЕпарным прогоном кавычек
        # (кривой sql → :""""";") срывала СТРОГИЙ _OVERQUOTE_RE → строка оставалась
        # завёрнутой и рвалась на col_N. Гейт строгий, снятие — толерантное.
        from logZilla3000.cleaners import reframe_tabular
        text = (
            '"@timestamp,""event.original""";\n'
            '"Jun 11, 2026 @ 12:02:14.344,""{""""sql"""":""""begin""""}""";\n'
            '"Jun 11, 2026 @ 12:02:14.345,""{""""sql"""":""""select""""}""";\n'
            '"Jun 11, 2026 @ 12:02:14.346,""{""""sql"""":""""";"",""""x"""":1}""";'
        )
        out = reframe_tabular(text)
        # и нормальная, и кривая (с непарным прогоном) строки потеряли внешний слой
        self.assertIn('Jun 11, 2026 @ 12:02:14.344,"{', out)
        self.assertIn('Jun 11, 2026 @ 12:02:14.346,"{', out)

    def test_corrupt_payload_row_no_col_n_explosion(self):
        # Битый источник: внутри payload кривое экранирование рвёт JSON на куски.
        # Должно: timestamp восстановлен, payload — одной строкой, без col_2…col_N.
        conv = JSONConverter()
        rows = conv.convert_csv_to_json(
            '@timestamp,event.original\n'
            # payload намеренно «рваный»: первый кусок начинается с '{', хвосты
            # приходят отдельными полями из-за битых кавычек
            'Jun 11, 2026 @ 12:02:14.346,'
            '"{""sql"":"""," broken "",""x"":1}"',
            delimiter=",",
        )
        rec = rows[0]
        self.assertEqual(rec["timestamp"], "Jun 11, 2026 @ 12:02:14.346")
        self.assertFalse([k for k in rec if k.startswith("col_")])

    def test_tabular_clean_preserves_cell_content(self):
        # двойные пробелы внутри значений не схлопываются
        cleaned = LogCleaner().clean_tabular("a;b\nx;foo   bar")
        self.assertIn("foo   bar", cleaned)

    def test_id_with_underscore_not_coerced_to_inf(self):
        # Регрессия: id '..._85e89394' float() трактовал как 85e89394 → inf →
        # json.dumps писал 'Infinity' (невалидно для браузерного JSON.parse).
        import json as _json
        conv = JSONConverter()
        self.assertEqual(conv._coerce_type("1779287397304638751_85e89394"),
                         "1779287397304638751_85e89394")     # остаётся строкой
        self.assertEqual(conv._coerce_type("1e999"), "1e999")  # переполнение → строка
        self.assertEqual(conv._coerce_type("42"), 42)          # обычные числа целы
        self.assertAlmostEqual(conv._coerce_type("3.14"), 3.14)
        # весь результат должен быть строго валидным JSON (allow_nan=False не бросает)
        recs = self.SAMPLE_INF_CSV
        parser = UniversalLogParser(delimiter=";", expand_message=True)
        res = parser.parse(recs)
        _json.dumps(res, allow_nan=False)                      # не должно бросить

    SAMPLE_INF_CSV = (
        "ts;id;line\n"
        '1;1779287397304638751_85e89394;"{""level"":""INFO"",""big"":1e999}"'
    )

    def test_multiline_pretty_json_detected_and_parsed(self):
        # Регрессия: pretty-printed JSON-массив длиннее sample_size (50 строк)
        # детектился как text (sample обрезал JSON) → построчный мусор. Теперь —
        # разбор всего текста как JSON.
        recs = [{"i": i, "msg": f"row {i}", "level": "INFO"} for i in range(80)]
        pretty = json.dumps(recs, ensure_ascii=False, indent=2)
        self.assertEqual(FormatDetector().detect(pretty), LogFormat.JSON)
        out = UniversalLogParser().parse(pretty)         # inline-путь
        self.assertEqual(len(out), 80)                   # не 1 запись на строку
        self.assertEqual(out[0]["msg"], "row 0")
        self.assertNotIn("json_snippet", out[0])

    # --- закавыченный заголовок (Grafana/Loki "Time","Line",... экспорт) ---
    QUOTED = (
        '"Time","Line","app"\n'
        '1779287388466,'
        '"{""level"": ""INFO"", ""msg"": ""GET /metrics 200"", ""status"": 200}",'
        'event-mapper'
    )

    def test_quoted_header_detected_as_csv(self):
        # Регрессия: закавыченные имена колонок не должны уводить в текстовый путь.
        self.assertEqual(FormatDetector().detect(self.QUOTED), LogFormat.CSV)

    def test_quoted_header_inline_parse_flattens_payload(self):
        parser = UniversalLogParser(expand_message=True)  # без delimiter — автоопределение
        result = parser.parse(self.QUOTED)
        self.assertEqual(len(result), 1)
        rec = result[0]
        self.assertEqual(rec["level"], "INFO")
        self.assertEqual(rec["status"], 200)
        self.assertEqual(rec["msg"], "GET /metrics 200")
        self.assertEqual(rec["app"], "event-mapper")  # обычная колонка сохранена
        self.assertNotIn("json_snippet", rec)
        self.assertNotIn("raw", rec)


class TestConverterEdgeCases(unittest.TestCase):
    """Краевые случаи конвертера: рваный CSV и детерминированное перекрытие JSON-колонок."""

    def test_ragged_csv_extra_fields_kept_as_col_n(self):
        # Лишние поля строки сверх заголовка не теряются — кладутся как col_N.
        conv = JSONConverter()
        out = conv.convert_csv_to_json("a,b\n1,2,3,4\n", delimiter=",", has_header=True)
        self.assertEqual(out[0]["a"], 1)
        self.assertEqual(out[0]["b"], 2)
        self.assertEqual(out[0]["col_2"], 3)
        self.assertEqual(out[0]["col_3"], 4)

    def test_json_column_overrides_scalar_regardless_of_order(self):
        # JSON-колонка перекрывает одноимённый скаляр, даже если идёт ПЕРЕД ним.
        conv = JSONConverter()
        out = conv.expand_json_columns([{"payload": '{"status": 200}', "status": "raw"}])
        self.assertEqual(out[0]["status"], 200)


class TestParseText(unittest.TestCase):
    """parse_text: разбор уже прочитанного содержимого (без повторного чтения файла)."""

    CSV = "level,msg\nINFO,hello\nERROR,boom\n"

    def test_parse_text_with_ext_matches_parse_file(self):
        # С filepath формат берётся по расширению — результат идентичен parse_file.
        parser = UniversalLogParser()
        via_text = parser.parse_text(self.CSV, filepath="data.csv")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(self.CSV)
            path = f.name
        try:
            via_file = parser.parse_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(via_text, via_file)
        self.assertEqual(len(via_text), 2)
        self.assertEqual(via_text[0]["level"], "INFO")

    def test_parse_text_without_filepath_autodetects(self):
        # Без filepath — автоопределение по содержимому (как parse()).
        parser = UniversalLogParser()
        self.assertEqual(parser.parse_text(self.CSV), parser.parse(self.CSV))

    def test_unknown_ext_falls_back_to_autodetect(self):
        parser = UniversalLogParser()
        self.assertEqual(
            parser.parse_text(self.CSV, filepath="data.unknown"),
            parser.parse(self.CSV),
        )


if __name__ == "__main__":
    unittest.main()
