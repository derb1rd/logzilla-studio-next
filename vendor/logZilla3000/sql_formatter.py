"""
Модуль форматирования SQL-запросов внутри JSON-логов.

Использует библиотеку sqlparse для красивого форматирования SQL:
- Ключевые слова в верхнем регистре
- Отступы для подзапросов
- Чистые переносы строк вместо \\n / \\t

Также обеспечивает пост-обработку JSON-строки, чтобы в выходном файле
SQL-запросы содержали реальные переносы строк вместо escape-последовательностей.
"""

import json
import re
import logging
from functools import lru_cache
from typing import Any

# Ключи, в которых могут лежать позиционные аргументы к SQL-запросу
ARGS_KEYS = {"args", "parameters", "bind_parameters", "query_args", "query_parameters"}

logger = logging.getLogger(__name__)

# Кешируем результат импорта sqlparse: модуль (если есть) или None. Предупреждение
# об отсутствии печатаем один раз — format_sql вызывается на каждую SQL-запись,
# иначе лог заливается дублями и раздувается счётчик warnings парсинга.
_sqlparse = None
_sqlparse_missing = False

# Порог длины SQL: очень длинные запросы дороги для sqlparse, а пользовательской
# ценности от их форматирования мало. Строки сверх лимита возвращаются как есть.
_MAX_SQL_LEN = 100_000

# Ключи, в которых ожидается SQL-запрос
SQL_KEYS = {"sql", "query", "statement", "query_text"}

# SQL-ключевые слова для определения, что строка является SQL-запросом
_SQL_START_PATTERN = re.compile(
    r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|WITH|ALTER|CREATE|DROP|TRUNCATE"
    r"|GRANT|REVOKE|EXPLAIN|MERGE|UPSERT|CALL|DO|BEGIN|COMMIT|ROLLBACK"
    r"|PREPARE|EXECUTE|DEALLOCATE|LISTEN|NOTIFY|SET|SHOW|COPY|VACUUM"
    r"|REINDEX|CLUSTER|LOCK|UNLOCK)\b",
    re.IGNORECASE | re.DOTALL,
)


def is_sql(value: str) -> bool:
    """
    Проверяет, похожа ли строка на SQL-запрос.

    Args:
        value: Строка для проверки

    Returns:
        True, если строка начинается с SQL-ключевого слова
    """
    if not isinstance(value, str) or len(value.strip()) < 6:
        return False
    return bool(_SQL_START_PATTERN.match(value))


@lru_cache(maxsize=1024)
def _format_sql_cached(value: str) -> str:
    """sqlparse-форматирование с кешем по точному содержимому запроса.

    Одинаковые SQL-строки (один SELECT на тысячи записей) форматируются
    ровно один раз — повторные вызовы возвращают закешированный результат
    без обращения к sqlparse. maxsize=1024 покрывает типичный диапазон
    уникальных запросов в одном файле (~100–500 уникальных SQL).
    """
    global _sqlparse, _sqlparse_missing
    if _sqlparse_missing:
        return value
    if _sqlparse is None:
        try:
            import sqlparse as _sp
            _sqlparse = _sp
        except ImportError:
            _sqlparse_missing = True
            logger.warning(
                "Библиотека sqlparse не установлена. "
                "Установите: pip install sqlparse"
            )
            return value
    try:
        formatted = _sqlparse.format(
            value,
            reindent=True,
            keyword_case="upper",
            strip_whitespace=True,
        )
        return formatted.strip()
    except Exception as exc:
        logger.debug("Не удалось отформатировать SQL: %s", exc)
        return value


def format_sql(value: str) -> str:
    """
    Форматирует SQL-запрос с помощью sqlparse.

    Args:
        value: Сырой SQL-запрос (возможно с \\n, \\t)

    Returns:
        Красиво отформатированный SQL-запрос

    Если sqlparse не установлен или строка не похожа на SQL —
    возвращает исходную строку без изменений.
    """
    if not isinstance(value, str):
        return value
    if len(value) > _MAX_SQL_LEN:
        return value
    if not is_sql(value):
        return value
    return _format_sql_cached(value)


def _parse_pg_array_literal(inner: str) -> list[str]:
    """Разбирает внутренность PostgreSQL-массива {a,"b c",NULL} в список строк."""
    items: list[str] = []
    current: list[str] = []
    in_quotes = False
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == '"' and not in_quotes:
            in_quotes = True
        elif c == '"' and in_quotes:
            if i + 1 < len(inner) and inner[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            in_quotes = False
        elif c == ',' and not in_quotes:
            items.append(''.join(current).strip())
            current = []
        else:
            current.append(c)
        i += 1
    items.append(''.join(current).strip())
    return items


def _parse_args(args: Any) -> list[str] | None:
    """Разбирает поле args в упорядоченный список строк. None при неразборе."""
    if isinstance(args, list):
        out = []
        for a in args:
            if isinstance(a, (dict, list)):
                out.append(json.dumps(a, ensure_ascii=False))
            else:
                out.append(str(a))
        return out
    if not isinstance(args, str):
        return None
    s = args.strip()
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(a) for a in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
    if s.startswith("{") and s.endswith("}"):
        return _parse_pg_array_literal(s[1:-1])
    return None


def _quote_arg(value: str) -> str:
    """Форматирует аргумент для вставки в SQL (только для отображения, не для выполнения)."""
    s = value.strip()
    if s.upper() == "NULL":
        return "NULL"
    try:
        int(s)
        return s
    except ValueError:
        pass
    try:
        float(s)
        return s
    except ValueError:
        pass
    return "'" + s.replace("'", "''") + "'"


def bind_args_to_sql(sql: str, args: Any) -> str:
    """Подставляет позиционные аргументы ($1, $2 …) в SQL-запрос.

    Результат предназначен только для отображения, а не для передачи в СУБД.
    Если args не разобрался или список пуст — возвращает sql без изменений.
    """
    parsed = _parse_args(args)
    if not parsed:
        return sql

    def _replace(m: re.Match) -> str:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(parsed):
            return _quote_arg(parsed[idx])
        return m.group(0)

    return re.sub(r'\$(\d+)', _replace, sql)


def format_sql_fields(data: Any, enabled: bool = True, bind_args: bool = False) -> Any:
    """Рекурсивно обходит структуру данных, форматирует SQL-поля и подставляет args.

    Args:
        data: Структура данных (dict, list или примитив)
        enabled: Включено ли pretty-форматирование SQL через sqlparse
        bind_args: Подставлять ли позиционные аргументы ($1/$2…) из поля args

    Returns:
        Структура данных с обработанными SQL-полями
    """
    if not enabled and not bind_args:
        return data

    if isinstance(data, dict):
        # Ищем args-поле на текущем уровне (для bind_args)
        args_value = None
        if bind_args:
            for ak in ARGS_KEYS:
                if ak in data:
                    args_value = data[ak]
                    break

        result = {}
        for key, value in data.items():
            if isinstance(value, str) and key.lower() in SQL_KEYS:
                out = format_sql(value) if enabled else value
                if bind_args and args_value is not None:
                    out = bind_args_to_sql(out, args_value)
                result[key] = out
            elif isinstance(value, (dict, list)):
                result[key] = format_sql_fields(value, enabled=enabled, bind_args=bind_args)
            else:
                result[key] = value
        return result

    if isinstance(data, list):
        return [format_sql_fields(item, enabled=enabled, bind_args=bind_args) for item in data]

    return data


def unescape_sql_in_json(json_str: str) -> str:
    """
    Пост-обработка JSON-строки: заменяет \\n и \\t на реальные символы
    внутри значений SQL-полей (sql, query, statement, query_text).

    json.dump() всегда экранирует переносы строк как \\n, что делает
    SQL-запросы нечитаемыми в выходном файле. Эта функция превращает:

        "sql": "SELECT a\\nFROM b\\nWHERE c"

    в:

        "sql": "SELECT a
    FROM b
    WHERE c"

    Реализация через json.loads/json.dumps вместо ручного парсинга —
    безопасно и корректно обрабатывает все случаи JSON-экранирования.

    Args:
        json_str: JSON-строка после json.dumps()

    Returns:
        JSON-строка с реальными переносами в SQL-полях
    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        # Если JSON невалиден — возвращаем как есть
        return json_str

    # Рекурсивно обрабатываем структуру, подменяя SQL-поля
    _unescape_sql_values(data)

    # Сериализуем обратно с особым обработчиком для SQL-полей
    return _json_dumps_with_real_newlines(data)


def _unescape_sql_values(data: Any) -> None:
    """Рекурсивно заменяет \\n/\\t на реальные символы в SQL-полях (in-place)."""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str) and key.lower() in SQL_KEYS:
                # Заменяем escape-последовательности на реальные символы
                data[key] = value.replace("\\n", "\n").replace("\\t", "\t")
            elif isinstance(value, (dict, list)):
                _unescape_sql_values(value)
    elif isinstance(data, list):
        for item in data:
            _unescape_sql_values(item)


class _SQLStringWrapper(str):
    """Обёртка для строки, чтобы json.dumps не экранировал \\n и \\t.

    Используется как default-обработчик: json.dumps вызывает str() на объекте,
    но мы подменяем сериализацию, чтобы сохранить реальные переносы строк.
    """
    pass


def _json_dumps_with_real_newlines(data: Any) -> str:
    """Сериализует данные в JSON, сохраняя реальные \\n/\\t в SQL-полях.

    Двухпроходная стратегия:
    1. Обычный json.dumps с ensure_ascii=False
    2. Пост-обработка: находим SQL-ключи и заменяем экранированные \\n/\\t
       на реальные символы внутри их значений.
    """
    # Первый проход: обычная сериализация
    json_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    # Второй проход: заменяем \\n/\\t в SQL-полях
    result: list[str] = []
    i = 0
    length = len(json_str)

    # Паттерн для поиска SQL-ключа в JSON
    sql_key_pattern = re.compile(
        r'("(?:sql|query|statement|query_text)"\s*:\s*")',
        re.IGNORECASE,
    )

    while i < length:
        match = sql_key_pattern.match(json_str, i)
        if match:
            # Скопировать ключ и открывающую кавычку как есть
            result.append(match.group(0))
            i = match.end()

            # Теперь мы внутри строкового значения SQL-поля
            # Читаем до закрывающей неэкранированной кавычки,
            # заменяя \\n → \n и \\t → \t
            while i < length:
                ch = json_str[i]
                if ch == '\\' and i + 1 < length:
                    next_ch = json_str[i + 1]
                    if next_ch == 'n':
                        result.append('\n')
                        i += 2
                    elif next_ch == 't':
                        result.append('\t')
                        i += 2
                    elif next_ch == '"':
                        # Экранированная кавычка внутри SQL — сохраняем
                        result.append('\\"')
                        i += 2
                    elif next_ch == '\\':
                        # Двойной бэкслеш — сохраняем
                        result.append('\\\\')
                        i += 2
                    else:
                        result.append(ch)
                        result.append(next_ch)
                        i += 2
                elif ch == '"':
                    # Закрывающая кавычка — конец значения
                    result.append('"')
                    i += 1
                    break
                else:
                    result.append(ch)
                    i += 1
        else:
            result.append(json_str[i])
            i += 1

    return ''.join(result)
