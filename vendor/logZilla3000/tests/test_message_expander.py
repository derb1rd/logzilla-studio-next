"""
Тесты для модуля message_expander.
"""

import json
import unittest

from logZilla3000.message_expander import (
    _decode_unicode_escapes,
    _normalize_key,
    _sanitize_protobuf_blocks,
    _restore_protobuf_placeholders,
    _try_parse_json,
    _try_parse_python_repr,
    _try_parse_string_value,
    expand_message_fields,
)


class TestDecodeUnicodeEscapes(unittest.TestCase):
    """Тесты декодирования Unicode-escape последовательностей."""

    def test_cyrillic_unicode_escape(self):
        """Декодирование кириллических Unicode-escape."""
        result = _decode_unicode_escapes(
            "\\u0415\\u0440\\u044b\\u0448\\u0435\\u0432\\u0430"
        )
        self.assertEqual(result, "Ерышева")

    def test_no_unicode_escape(self):
        """Строка без Unicode-escape не изменяется."""
        result = _decode_unicode_escapes("Hello world")
        self.assertEqual(result, "Hello world")

    def test_dict_with_unicode_escapes(self):
        """Декодирование Unicode-escape в значениях dict."""
        result = _decode_unicode_escapes({
            "name": "\\u041e\\u043b\\u044c\\u0433\\u0430",
            "city": "Moscow",
        })
        self.assertEqual(result["name"], "Ольга")
        self.assertEqual(result["city"], "Moscow")

    def test_list_with_unicode_escapes(self):
        """Декодирование Unicode-escape в элементах list."""
        result = _decode_unicode_escapes([
            "\\u0410\\u043b\\u0435\\u043a\\u0441\\u0435\\u0439",
            "John",
        ])
        self.assertEqual(result[0], "Алексей")
        self.assertEqual(result[1], "John")

    def test_non_string_passthrough(self):
        """Числа и None проходят без изменений."""
        self.assertEqual(_decode_unicode_escapes(42), 42)
        self.assertIsNone(_decode_unicode_escapes(None))
        self.assertEqual(_decode_unicode_escapes(True), True)


class TestTryParseJson(unittest.TestCase):
    """Тесты парсинга JSON-строк."""

    def test_valid_json_dict(self):
        """Валидный JSON dict."""
        result = _try_parse_json('{"key": "value", "num": 42}')
        self.assertEqual(result, {"key": "value", "num": 42})

    def test_valid_json_list(self):
        """Валидный JSON list."""
        result = _try_parse_json('[1, 2, 3]')
        self.assertEqual(result, [1, 2, 3])

    def test_invalid_json(self):
        """Невалидный JSON возвращает None."""
        result = _try_parse_json("{'key': 'value'}")
        self.assertIsNone(result)

    def test_non_json_string(self):
        """Обычная строка возвращает None."""
        result = _try_parse_json("Hello world")
        self.assertIsNone(result)

    def test_empty_string(self):
        """Пустая строка возвращает None."""
        result = _try_parse_json("")
        self.assertIsNone(result)

    def test_non_string_input(self):
        """Не-строка возвращает None."""
        self.assertIsNone(_try_parse_json(42))
        self.assertIsNone(_try_parse_json(None))


class TestTryParsePythonRepr(unittest.TestCase):
    """Тесты парсинга Python dict repr."""

    def test_python_dict_single_quotes(self):
        """Python dict с одинарными кавычками."""
        result = _try_parse_python_repr("{'key': 'value'}")
        self.assertEqual(result, {"key": "value"})

    def test_python_dict_with_true_false_none(self):
        """Python dict с True/False/None."""
        result = _try_parse_python_repr("{'a': True, 'b': False, 'c': None}")
        self.assertEqual(result, {"a": True, "b": False, "c": None})

    def test_python_dict_with_numbers(self):
        """Python dict с числами."""
        result = _try_parse_python_repr("{'id': 4608, 'score': 3.14}")
        self.assertEqual(result, {"id": 4608, "score": 3.14})

    def test_json_double_quotes_not_parsed(self):
        """JSON с двойными кавычками не парсится как Python repr."""
        # JSON с двойными кавычками и без True/False/None
        # не имеет признаков Python repr
        result = _try_parse_python_repr('{"key": "value"}')
        self.assertIsNone(result)

    def test_non_dict_string(self):
        """Строка не-dict возвращает None."""
        result = _try_parse_python_repr("Hello world")
        self.assertIsNone(result)

    def test_empty_string(self):
        """Пустая строка возвращает None."""
        result = _try_parse_python_repr("")
        self.assertIsNone(result)

    def test_protobuf_block_sanitized(self):
        """Python dict с протобаф-блоком парсится корректно."""
        value = "{'state': [field: \"status\"\\nbefore_value: \"generated\"\\nafter_value: \"done\"\\n], 'success': True}"
        result = _try_parse_python_repr(value)
        self.assertIsNotNone(result)
        self.assertTrue(result["success"])
        # state должен быть строкой (протобаф-блок)
        self.assertIsInstance(result["state"], str)
        self.assertIn("field:", result["state"])


class TestSanitizeProtobufBlocks(unittest.TestCase):
    """Тесты санитизации протобаф-подобных блоков."""

    def test_no_protobuf_blocks(self):
        """Строка без протобаф-блоков не изменяется."""
        value = "{'key': 'value', 'num': 42}"
        result, placeholders = _sanitize_protobuf_blocks(value)
        self.assertEqual(result, value)
        self.assertEqual(len(placeholders), 0)

    def test_single_protobuf_block(self):
        """Один протобаф-блок заменяется на плейсхолдер."""
        value = "{'state': [field: \"status\"\\nbefore_value: \"old\"\\n], 'ok': True}"
        result, placeholders = _sanitize_protobuf_blocks(value)
        self.assertEqual(len(placeholders), 1)
        self.assertNotIn("[field:", result)
        self.assertIn("__PROTOBUF_BLOCK_0__", result)

    def test_multiple_protobuf_blocks(self):
        """Несколько протобаф-блоков заменяются на плейсхолдеры."""
        value = "{'a': [field: \"x\"\\n], 'b': [field: \"y\"\\n]}"
        result, placeholders = _sanitize_protobuf_blocks(value)
        self.assertEqual(len(placeholders), 2)


class TestRestoreProtobufPlaceholders(unittest.TestCase):
    """Тесты восстановления протобаф-блоков из плейсхолдеров."""

    def test_restore_in_dict(self):
        """Восстановление плейсхолдера в значении dict."""
        block = '[field: "status"\\nbefore_value: "old"\\n]'
        placeholders = {"__PROTOBUF_BLOCK_0__": block}
        obj = {"state": "__PROTOBUF_BLOCK_0__", "ok": True}
        result = _restore_protobuf_placeholders(obj, placeholders)
        self.assertEqual(result["state"], block)
        self.assertTrue(result["ok"])

    def test_no_placeholders(self):
        """Без плейсхолдеров объект не изменяется."""
        obj = {"key": "value"}
        result = _restore_protobuf_placeholders(obj, {})
        self.assertEqual(result, obj)


class TestNormalizeKey(unittest.TestCase):
    """Тесты нормализации ключей."""

    def test_known_mapping(self):
        """Известные ключи маппятся по KEY_MAPPING."""
        self.assertEqual(_normalize_key("Service"), "service")
        self.assertEqual(_normalize_key("Name"), "logger_name")
        self.assertEqual(_normalize_key("Levelname"), "level")
        self.assertEqual(_normalize_key("FuncName"), "func_name")
        self.assertEqual(_normalize_key("Message"), "message")

    def test_camel_case_to_snake(self):
        """CamelCase ключи преобразуются в snake_case."""
        self.assertEqual(_normalize_key("RequestId"), "request_id")
        self.assertEqual(_normalize_key("UserId"), "user_id")

    def test_already_snake_case(self):
        """snake_case ключи не изменяются."""
        self.assertEqual(_normalize_key("request_id"), "request_id")


class TestTryParseStringValue(unittest.TestCase):
    """Тесты раскрытия строковых значений."""

    def test_json_string(self):
        """JSON-строка раскрывается."""
        result = _try_parse_string_value('{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_python_repr_string(self):
        """Python dict repr раскрывается."""
        result = _try_parse_string_value("{'key': 'value', 'flag': True}")
        self.assertEqual(result, {"key": "value", "flag": True})

    def test_plain_string(self):
        """Обычная строка не раскрывается."""
        result = _try_parse_string_value("Hello world")
        self.assertEqual(result, "Hello world")


class TestExpandMessageFields(unittest.TestCase):
    """Интеграционные тесты раскрытия message."""

    def test_simple_syslog_with_json_message(self):
        """Раскрытие syslog-записи с JSON в message."""
        record = {
            "timestamp": "Apr 30 11:31:26",
            "host": "nm-prd-app-ms1",
            "app": "audit_service",
            "pid": 143598,
            "message": json.dumps({
                "Service": "audit",
                "Name": "KafkaProducer",
                "Levelno": 10,
                "Levelname": "DEBUG",
                "Module": "kafka",
                "Lineno": 224,
                "FuncName": "_process",
                "Created": 1777537886.019,
                "Asctime": "2026-04-30 11:31:26,019",
                "Msecs": 19.0,
                "RelativeCreated": 1188178028.048,
                "Thread": 139763285726976,
                "ThreadName": "Thread-3 (_process)",
                "Process": 143598,
                "Message": "Queue is empty",
            }),
        }
        result = expand_message_fields(record)

        # Проверяем flatten: logging-поля подняты наверх
        self.assertEqual(result["service"], "audit")
        self.assertEqual(result["logger_name"], "KafkaProducer")
        self.assertEqual(result["level"], "DEBUG")
        self.assertEqual(result["module"], "kafka")
        self.assertEqual(result["func_name"], "_process")
        self.assertEqual(result["asctime"], "2026-04-30 11:31:26,019")
        self.assertEqual(result["thread_name"], "Thread-3 (_process)")

        # Шумовые поля отфильтрованы
        self.assertNotIn("Levelno", result)
        self.assertNotIn("Created", result)
        self.assertNotIn("Msecs", result)
        self.assertNotIn("RelativeCreated", result)
        self.assertNotIn("Thread", result)
        self.assertNotIn("Process", result)

        # message = продуктовый payload
        self.assertEqual(result["message"], "Queue is empty")

        # Исходные syslog-поля сохранены
        self.assertEqual(result["timestamp"], "Apr 30 11:31:26")
        self.assertEqual(result["host"], "nm-prd-app-ms1")
        self.assertEqual(result["pid"], 143598)

    def test_syslog_with_python_dict_payload(self):
        """Раскрытие syslog-записи с Python dict repr в Message."""
        record = {
            "timestamp": "Apr 30 11:31:41",
            "host": "nm-prd-app-ms1",
            "app": "audit_service",
            "pid": 143598,
            "message": json.dumps({
                "Service": "audit",
                "Name": "AuditConsumer",
                "Levelno": 20,
                "Levelname": "INFO",
                "Module": "audit_consumer",
                "Lineno": 120,
                "FuncName": "write",
                "Created": 1777537901.346,
                "Asctime": "2026-04-30 11:31:41,346",
                "Msecs": 346.0,
                "RelativeCreated": 1188193355.494,
                "Thread": 139763310905088,
                "ThreadName": "Thread-2 (_process)",
                "Process": 143598,
                "Message": "{'request_id': 'abc123', 'user_id': 4608, 'success': True}",
            }),
        }
        result = expand_message_fields(record)

        # message должен быть раскрытым Python dict
        self.assertIsInstance(result["message"], dict)
        self.assertEqual(result["message"]["request_id"], "abc123")
        self.assertEqual(result["message"]["user_id"], 4608)
        self.assertTrue(result["message"]["success"])

    def test_unicode_escape_in_payload(self):
        """Декодирование Unicode-escape в продуктовом payload."""
        record = {
            "timestamp": "Apr 30 11:31:41",
            "host": "nm-prd-app-ms1",
            "app": "audit_service",
            "pid": 143598,
            "message": json.dumps({
                "Service": "audit",
                "Name": "AuditConsumer",
                "Levelname": "INFO",
                "Message": "{'name': '\\u0415\\u0440\\u044b\\u0448\\u0435\\u0432\\u0430'}",
            }),
        }
        result = expand_message_fields(record)
        self.assertEqual(result["message"]["name"], "Ерышева")

    def test_disabled_expansion(self):
        """При enabled=False сообщение не раскрывается."""
        record = {
            "timestamp": "Apr 30 11:31:26",
            "message": '{"Service": "audit", "Message": "test"}',
        }
        result = expand_message_fields(record, enabled=False)
        self.assertEqual(result["message"], '{"Service": "audit", "Message": "test"}')

    def test_non_json_message(self):
        """Сообщение без JSON остаётся как есть."""
        record = {
            "timestamp": "Apr 30 11:31:26",
            "host": "nm-prd-app-ms1",
            "message": "Simple text message",
        }
        result = expand_message_fields(record)
        self.assertEqual(result["message"], "Simple text message")

    def test_list_of_records(self):
        """Обработка списка записей."""
        records = [
            {
                "timestamp": "Apr 30 11:31:26",
                "message": json.dumps({
                    "Service": "audit",
                    "Levelname": "DEBUG",
                    "Message": "Queue is empty",
                }),
            },
            {
                "timestamp": "Apr 30 11:31:27",
                "message": "Simple text",
            },
        ]
        result = expand_message_fields(records)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["level"], "DEBUG")
        self.assertEqual(result[0]["message"], "Queue is empty")
        self.assertEqual(result[1]["message"], "Simple text")

    def test_no_message_field(self):
        """Запись без поля message не ломается."""
        record = {
            "timestamp": "Apr 30 11:31:26",
            "host": "nm-prd-app-ms1",
        }
        result = expand_message_fields(record)
        self.assertNotIn("message", result)
        self.assertEqual(result["timestamp"], "Apr 30 11:31:26")

    def test_custom_noise_fields(self):
        """Кастомный набор шумовых полей."""
        record = {
            "timestamp": "Apr 30 11:31:26",
            "message": json.dumps({
                "Service": "audit",
                "Levelname": "DEBUG",
                "Lineno": 224,
                "Message": "test",
            }),
        }
        # По умолчанию Lineno НЕ в noise_fields
        result = expand_message_fields(record)
        self.assertIn("lineno", result)

        # Добавляем Lineno в noise_fields
        result = expand_message_fields(record, noise_fields={"Lineno"})
        self.assertNotIn("lineno", result)

    def test_empty_message(self):
        """Пустое сообщение в JSON."""
        record = {
            "timestamp": "Apr 30 11:31:41",
            "message": json.dumps({
                "Service": "audit",
                "Name": "AuditConsumer",
                "Levelname": "ERROR",
                "Message": "",
            }),
        }
        result = expand_message_fields(record)
        self.assertEqual(result["level"], "ERROR")
        self.assertEqual(result["message"], "")

    def test_nested_json_in_message(self):
        """Вложенный JSON в Message раскрывается рекурсивно."""
        record = {
            "timestamp": "Apr 30 11:31:41",
            "message": json.dumps({
                "Service": "audit",
                "Levelname": "INFO",
                "Message": '{"nested_key": "nested_value"}',
            }),
        }
        result = expand_message_fields(record)
        self.assertIsInstance(result["message"], dict)
        self.assertEqual(result["message"]["nested_key"], "nested_value")


class TestExpandWithProtobuf(unittest.TestCase):
    """Тесты раскрытия message с протобаф-подобными блоками."""

    def test_protobuf_block_preserved_as_string(self):
        """Протобаф-блок в state сохраняется как строка."""
        record = {
            "timestamp": "Apr 30 11:31:41",
            "message": json.dumps({
                "Service": "audit",
                "Name": "AuditConsumer",
                "Levelname": "INFO",
                "Message": "{'state': [field: \"status\"\\nbefore_value: \"generated\"\\nafter_value: \"done\"\\n], 'success': True}",
            }),
        }
        result = expand_message_fields(record)
        self.assertIsInstance(result["message"], dict)
        self.assertIsInstance(result["message"]["state"], str)
        self.assertIn("field:", result["message"]["state"])
        self.assertTrue(result["message"]["success"])


if __name__ == "__main__":
    unittest.main()
