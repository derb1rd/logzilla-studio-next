"""
Модуль раскрытия вложенных JSON/Python-dict в поле message.

Продуктовые логи (syslog) часто содержат в поле message вложенную
JSON-строку с полями Python logging, внутри которых лежит продуктовый
payload. Этот модуль раскрывает все уровни вложенности, фильтрует
шумовые поля и нормализует ключи.

Пример преобразования:

    До:
    {
      "timestamp": "Apr 30 11:31:41",
      "host": "nm-prd-app-ms1",
      "app": "audit_service",
      "pid": 143598,
      "message": "{\\"Service\\": \\"audit\\", \\"Name\\": \\"AuditConsumer\\",
                  \\"Levelname\\": \\"INFO\\", \\"Message\\": \\"{'request_id': 'abc', ...}\\"}"
    }

    После:
    {
      "timestamp": "Apr 30 11:31:41",
      "host": "nm-prd-app-ms1",
      "app": "audit_service",
      "pid": 143598,
      "service": "audit",
      "logger_name": "AuditConsumer",
      "level": "INFO",
      "message": {"request_id": "abc", ...}
    }
"""

import ast
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Шумовые поля Python logging, которые дублируют информацию
DEFAULT_NOISE_FIELDS: frozenset[str] = frozenset({
    "Levelno",
    "Created",
    "Msecs",
    "RelativeCreated",
    "Thread",
    "Process",
})

# Маппинг ключей Python logging → нормализованные ключи
KEY_MAPPING: dict[str, str] = {
    "Service": "service",
    "Name": "logger_name",
    "Levelname": "level",
    "Module": "module",
    "FuncName": "func_name",
    "Asctime": "asctime",
    "ThreadName": "thread_name",
    "Lineno": "lineno",
    "Message": "message",
}

# Поля, которые поднимаются из вложенного logging-объекта в родительскую запись
FLATTEN_FIELDS: frozenset[str] = frozenset(KEY_MAPPING.keys())


def _decode_unicode_escapes(obj: Any) -> Any:
    """
    Рекурсивно декодирует Unicode-escape последовательности в строках.

    Превращает \\u0415\\u0440\\u044b\\u0448\\u0435\\u0432\\u0430 → Ерышева

    Args:
        obj: Строка, dict, list или примитив

    Returns:
        Объект с декодированными Unicode-escape последовательностями
    """
    if isinstance(obj, str):
        try:
            # Пытаемся декодировать unicode-escape, но только если
            # строка содержит \\u последовательности
            if "\\u" in obj:
                # bytes(obj, 'utf-8').decode('unicode_escape') ломает
                # кириллицу которая уже нормально отображается,
                # поэтому декодируем только \\u-паттерны
                decoded = re.sub(
                    r"\\u([0-9a-fA-F]{4})",
                    lambda m: chr(int(m.group(1), 16)),
                    obj,
                )
                # Не-BMP символы (эмодзи и т.п.) приходят суррогатной парой
                # \\uD83D\\uDE00 — поэлементный chr() даёт ДВА одиночных суррогата.
                # json.dumps(...).encode('utf-8') на одиночном суррогате падает
                # (UnicodeEncodeError) и роняет весь ответ /api/parse. Round-trip
                # через utf-16 склеивает пары обратно в символ (😀), а одиночные
                # суррогаты заменяет на U+FFFD — строка гарантированно валидна.
                if any(0xD800 <= ord(c) <= 0xDFFF for c in decoded):
                    decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
                return decoded
        except Exception:
            logger.debug("Не удалось декодировать unicode-escape: %s...", obj[:50])
        return obj

    if isinstance(obj, dict):
        return {_decode_unicode_escapes(k): _decode_unicode_escapes(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_decode_unicode_escapes(item) for item in obj]

    return obj


def _try_parse_json(value: str) -> Optional[Any]:
    """
    Пытается распарсить строку как JSON.

    Args:
        value: Строка для парсинга

    Returns:
        Распарсенный объект или None, если строка не является JSON
    """
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    # Быстрая проверка: JSON должен начинаться с { или [
    if stripped[0] not in ('{', '['):
        return None

    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None


# Паттерн для обнаружения протобаф-подобных блоков: [field: "value"\n...]
_PROTOBUF_BLOCK_PATTERN = re.compile(
    r"\[field:\s*(?:\"[^\"]*\"|'[^']*')",
    re.DOTALL,
)


def _sanitize_protobuf_blocks(value: str) -> tuple[str, dict[str, str]]:
    """
    Заменяет протобаф-подобные блоки [field: "..."\\n...] на строковые
    плейсхолдеры, чтобы ast.literal_eval мог распарсить остальной dict.

    Протобаф-формат: [field: "status"\\nbefore_value: "generated"\\n...]
    не является валидным Python, поэтому его нужно вырезать до парсинга.

    Args:
        value: Строка с возможными протобаф-блоками

    Returns:
        Кортеж (санитизированная строка, словарь плейсхолдеров)
    """
    placeholders: dict[str, str] = {}
    if "[field:" not in value:
        return value, placeholders

    result = value
    idx = 0
    while idx < len(result):
        # Ищем начало протобаф-блока
        pos = result.find("[field:", idx)
        if pos == -1:
            break

        # Находим соответствующую закрывающую скобку
        depth = 0
        end = pos
        in_string = False
        string_char = None

        while end < len(result):
            ch = result[end]

            if in_string:
                if ch == '\\' and end + 1 < len(result):
                    end += 2  # skip escaped char
                    continue
                if ch == string_char:
                    in_string = False
            else:
                if ch in ('"', "'"):
                    in_string = True
                    string_char = ch
                elif ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        end += 1
                        break
            end += 1

        if depth != 0:
            # Не нашли закрывающую скобку — пропускаем
            idx = pos + 1
            continue

        # Извлекаем блок и заменяем на плейсхолдер
        block = result[pos:end]
        placeholder_key = f"__PROTOBUF_BLOCK_{len(placeholders)}__"
        placeholders[placeholder_key] = block
        result = result[:pos] + repr(placeholder_key) + result[end:]
        idx = pos + len(repr(placeholder_key))

    return result, placeholders


def _restore_protobuf_placeholders(
    obj: Any,
    placeholders: dict[str, str],
) -> Any:
    """
    Восстанавливает протобаф-блоки из плейсхолдеров после парсинга.

    Args:
        obj: Распарсенный объект
        placeholders: Словарь плейсхолдеров

    Returns:
        Объект с восстановленными протобаф-блоками (как строки)
    """
    if not placeholders:
        return obj

    if isinstance(obj, str):
        if obj in placeholders:
            return placeholders[obj]
        return obj

    if isinstance(obj, dict):
        return {
            _restore_protobuf_placeholders(k, placeholders):
            _restore_protobuf_placeholders(v, placeholders)
            for k, v in obj.items()
        }

    if isinstance(obj, list):
        return [_restore_protobuf_placeholders(item, placeholders) for item in obj]

    return obj


def _try_parse_python_repr(value: str) -> Optional[Any]:
    """
    Пытается распарсить строку как Python dict repr.

    Python logging часто формирует repr(dict) с одинарными кавычками,
    True/False/None — это не валидный JSON, но ast.literal_eval справляется.

    Если строка содержит протобаф-подобные блоки [field: "..."\\n...],
    они предварительно заменяются на плейсхолдеры и восстанавливаются
    после парсинга как строки.

    Args:
        value: Строка для парсинга

    Returns:
        Распарсенный объект или None, если строка не является Python repr
    """
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    # Быстрая проверка: должен начинаться с { или [
    if stripped[0] not in ('{', '[',):
        return None

    # Проверяем наличие признаков Python repr:
    # одинарные кавычки, True/False/None
    has_single_quotes = "'" in stripped
    has_python_literals = any(
        word in stripped
        for word in ("True", "False", "None")
    )

    if not (has_single_quotes or has_python_literals):
        return None

    # Санитизация протобаф-подобных блоков
    sanitized, placeholders = _sanitize_protobuf_blocks(stripped)

    try:
        result = ast.literal_eval(sanitized)
        # Убеждаемся, что результат — dict или list
        if isinstance(result, (dict, list)):
            # Восстанавливаем плейсхолдеры
            result = _restore_protobuf_placeholders(result, placeholders)
            return result
        return None
    except (ValueError, SyntaxError):
        return None


def _try_parse_string_value(value: str) -> Any:
    """
    Пытается раскрыть строковое значение: сначала JSON, потом Python repr.

    Args:
        value: Строка для раскрытия

    Returns:
        Раскрытый объект или исходная строка
    """
    # Сначала пробуем JSON (самый частый случай)
    parsed = _try_parse_json(value)
    if parsed is not None:
        return parsed

    # Потом пробуем Python dict repr
    parsed = _try_parse_python_repr(value)
    if parsed is not None:
        return parsed

    # Не удалось распарсить — возвращаем как есть
    return value


def _normalize_key(key: str) -> str:
    """
    Нормализует ключ: применяет маппинг для известных полей,
    для остальных — приводит к snake_case.

    Args:
        key: Исходный ключ

    Returns:
        Нормализованный ключ
    """
    if key in KEY_MAPPING:
        return KEY_MAPPING[key]

    # Для неизвестных ключей: CamelCase → snake_case
    # Например: RequestId → request_id
    snake = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    snake = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", snake)
    return snake.lower()


def _expand_nested_strings(obj: Any) -> Any:
    """
    Рекурсивно раскрывает строковые значения внутри dict/list,
    которые содержат JSON или Python dict repr.

    Args:
        obj: Объект для раскрытия

    Returns:
        Объект с раскрытыми вложенными строками
    """
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if isinstance(value, str):
                parsed = _try_parse_string_value(value)
                if isinstance(parsed, (dict, list)):
                    result[key] = _expand_nested_strings(parsed)
                else:
                    result[key] = parsed
            elif isinstance(value, (dict, list)):
                result[key] = _expand_nested_strings(value)
            else:
                result[key] = value
        return result

    if isinstance(obj, list):
        return [_expand_nested_strings(item) for item in obj]

    return obj


def _expand_logging_message(
    record: dict[str, Any],
    noise_fields: frozenset[str] = DEFAULT_NOISE_FIELDS,
) -> dict[str, Any]:
    """
    Раскрывает вложенный Python logging JSON в поле message записи.

    Стратегия:
    1. Раскрыть JSON в message → получить поля logging
    2. Отфильтровать шумовые поля
    3. Нормализовать ключи
    4. Поднять поля logging в родительскую запись (flatten)
    5. message = продуктовый payload (раскрытый из внутреннего Message)

    Args:
        record: Запись лога с полем message
        noise_fields: Множество шумовых полей для фильтрации

    Returns:
        Запись с раскрытым message
    """
    if "message" not in record:
        return record

    message_value = record["message"]
    if not isinstance(message_value, str):
        return record

    # Шаг 1: Раскрыть JSON в message
    parsed = _try_parse_json(message_value)
    if not isinstance(parsed, dict):
        # Не JSON — пробуем Python repr
        parsed = _try_parse_python_repr(message_value)
        if not isinstance(parsed, dict):
            # Не удалось распарсить — оставляем как есть
            return record

    # Шаг 2: Раскрыть вложенные строковые значения внутри parsed
    parsed = _expand_nested_strings(parsed)

    # Шаг 3: Декодировать unicode-escape
    parsed = _decode_unicode_escapes(parsed)

    # Шаг 4: Разделить на logging-поля и продуктовый payload
    logging_fields = {}
    product_payload = {}

    for key, value in parsed.items():
        if key in noise_fields:
            # Шумовое поле — пропускаем
            continue
        if key in FLATTEN_FIELDS:
            # Поле logging — поднимаем наверх
            normalized_key = _normalize_key(key)
            logging_fields[normalized_key] = value
        else:
            # Продуктовое поле — оставляем в message
            product_payload[key] = value

    # Шаг 5: Собрать итоговую запись
    result = dict(record)  # копируем исходную запись

    # Удаляем исходное message — оно будет заменено
    del result["message"]

    # Добавляем logging-поля (flatten)
    result.update(logging_fields)

    # message = продуктовый payload
    if "message" in logging_fields:
        # Внутренний Message уже обработан — это продуктовый payload
        inner_message = logging_fields["message"]
        if isinstance(inner_message, (dict, list)):
            result["message"] = inner_message
        elif isinstance(inner_message, str):
            # Попробуем раскрыть строковый Message ещё раз
            expanded = _try_parse_string_value(inner_message)
            if isinstance(expanded, (dict, list)):
                expanded = _expand_nested_strings(expanded)
                expanded = _decode_unicode_escapes(expanded)
                result["message"] = expanded
            else:
                result["message"] = expanded
        else:
            result["message"] = inner_message
    elif product_payload:
        # Если внутреннего Message нет, но есть другие продуктовые поля
        result["message"] = product_payload
    else:
        # Нет ни Message, ни продуктовых полей
        result["message"] = ""

    return result


def expand_message_fields(
    data: Any,
    enabled: bool = True,
    noise_fields: Optional[set[str]] = None,
) -> Any:
    """
    Рекурсивно обходит структуру данных и раскрывает вложенные
    JSON/Python-dict в полях message.

    Args:
        data: Структура данных (dict, list или примитив)
        enabled: Включено ли раскрытие message
        noise_fields: Набор шумовых полей для фильтрации
            (None = использовать DEFAULT_NOISE_FIELDS)

    Returns:
        Структура данных с раскрытыми message-полями
    """
    if not enabled:
        return data

    noise = frozenset(noise_fields) if noise_fields else DEFAULT_NOISE_FIELDS

    if isinstance(data, dict):
        # Проверяем, есть ли поле message для раскрытия
        if "message" in data and isinstance(data["message"], str):
            return _expand_logging_message(data, noise_fields=noise)
        # Рекурсивно обходим вложенные dict/list
        result = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                result[key] = expand_message_fields(value, enabled=True, noise_fields=noise)
            else:
                result[key] = value
        return result

    if isinstance(data, list):
        return [expand_message_fields(item, enabled=True, noise_fields=noise) for item in data]

    return data
