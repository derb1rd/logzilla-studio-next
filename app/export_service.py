"""ExportService — сериализация результата парсинга в JSON (+ опциональный gzip).

Раньше поддерживались json/csv/txt/xml; по решению заказчика выгрузка всегда JSON
(прочие форматы не нужны). gzip — опциональное сжатие поверх JSON.
"""

from __future__ import annotations

import gzip
import json
import re as _re
from datetime import datetime, timezone

from . import __version__
from .contract import ExportOptions

try:
    from logZilla3000.sql_formatter import unescape_sql_in_json as _unescape_sql
except ImportError:
    _unescape_sql = None  # type: ignore[assignment]

# SQL-ключи, в которых нужны реальные переносы строк в выгрузке.
_SQL_KEY_RE = _re.compile(
    r'"(?:sql|query|statement|query_text)"\s*:\s*"',
    _re.IGNORECASE,
)


def _apply_sql_newlines(json_str: str) -> str:
    """Заменяет \\\\n/\\\\t → реальные символы в SQL-полях JSON-строки.

    Работает на уровне текста (без пересериализации), поэтому форматирование
    от _smart_json остаётся нетронутым.
    """
    result: list[str] = []
    i = 0
    s = json_str
    n = len(s)
    while i < n:
        m = _SQL_KEY_RE.match(s, i)
        if m:
            result.append(m.group())
            i = m.end()
            while i < n:
                ch = s[i]
                if ch == "\\" and i + 1 < n:
                    nch = s[i + 1]
                    if nch == "n":
                        result.append("\n"); i += 2
                    elif nch == "t":
                        result.append("\t"); i += 2
                    else:
                        result.append(ch + nch); i += 2
                elif ch == '"':
                    result.append('"'); i += 1; break
                else:
                    result.append(ch); i += 1
        else:
            result.append(s[i]); i += 1
    return "".join(result)


def _dumps(obj, **kw) -> str:
    """json.dumps → str, гарантированно кодируемая в utf-8.

    json.dumps(ensure_ascii=False) НЕ падает на одиночных суррогатах — он
    возвращает str с суррогатом внутри, а UnicodeEncodeError бросает уже
    .encode('utf-8') на стороне вызывающего, вне этого try. Поэтому проверяем
    кодируемость здесь и отступаем на ensure_ascii=True (суррогаты → \\udXXX,
    валидный JSON), как это делает server._send_json."""
    s = json.dumps(obj, ensure_ascii=False, **kw)
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        s = json.dumps(obj, ensure_ascii=True, **kw)
    return s


# Максимальная длина строки, при которой массив/объект сворачивается в одну строку.
_MAX_INLINE = 88


def _smart_json(value: object, level: int = 0, ensure_ascii: bool = False) -> str:
    """Рекурсивный pretty-printer: короткие массивы/объекты — на одну строку.

    Аналог jq --indent 2: если весь контейнер влезает в _MAX_INLINE символов
    (с учётом текущего отступа), он остаётся однострочным. Иначе раскрывается
    на несколько строк, но каждый вложенный контейнер снова пробует схлопнуться.

    Результат: массив из одного короткого элемента [\"error-translator:8888\"]
    остаётся на одной строке вместо трёх, не теряя читаемости.
    """
    ea = ensure_ascii

    def _scalar(v: object) -> str:
        return json.dumps(v, ensure_ascii=ea)

    if not isinstance(value, (dict, list)):
        return _scalar(value)

    # Пробуем компактную однострочную форму
    compact = json.dumps(value, ensure_ascii=ea, separators=(", ", ": "))
    if level * 2 + len(compact) <= _MAX_INLINE:
        return compact

    inner = "  " * (level + 1)
    outer = "  " * level

    if isinstance(value, list):
        parts = [inner + _smart_json(v, level + 1, ea) for v in value]
        return "[\n" + ",\n".join(parts) + "\n" + outer + "]"
    else:
        parts = [inner + _scalar(k) + ": " + _smart_json(v, level + 1, ea)
                 for k, v in value.items()]
        return "{\n" + ",\n".join(parts) + "\n" + outer + "}"


def _dumps_pretty(obj: dict) -> bytes:
    """Читаемый JSON-экспорт: короткие вложения — в одну строку, записи — через
    пустую строку друг от друга.

    Структура файла:
        {
          "_logzilla": {...},
          "records": [
            { ...запись 1... },

            { ...запись 2... },

            ...
          ]
        }

    Пустые строки между записями — валидный JSON (пробелы разрешены везде
    между токенами). SQL-постобработка применяется поверх.

    Суррогаты: если ensure_ascii=False возвращает некодируемую строку —
    падаем на безопасный ensure_ascii=True без SQL-переносов.
    """
    ea = False

    def _render(ea_flag: bool) -> str:
        meta_str = _smart_json(obj.get("_logzilla", {}), level=1, ensure_ascii=ea_flag)
        records = obj.get("records", [])
        if not records:
            return '{\n  "_logzilla": ' + meta_str + ',\n  "records": []\n}'

        record_strs = []
        for rec in records:
            body = _smart_json(rec, level=2, ensure_ascii=ea_flag)
            # _smart_json(level=2) уже расставляет отступы:
            #   первая строка «{» — без пробелов (добавляем 4 сами),
            #   поля — 6 пробелов, закрывающий «}» — 4 пробела.
            # Поэтому просто prepend'им «    » к первой строке («{»).
            record_strs.append("    " + body)

        records_block = ",\n\n".join(record_strs)
        return (
            '{\n'
            '  "_logzilla": ' + meta_str + ',\n'
            '  "records": [\n'
            + records_block + "\n"
            '  ]\n'
            '}'
        )

    raw = _render(ea)
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        raw = _render(True)  # ensure_ascii fallback

    # SQL postprocessing: apply real newlines in SQL fields without re-serializing.
    # _unescape_sql calls json.dumps(indent=2) internally and would destroy our
    # smart formatting; _apply_sql_newlines does the same character-level fix in place.
    cooked = _apply_sql_newlines(raw)
    try:
        return cooked.encode("utf-8")
    except UnicodeEncodeError:
        return raw.encode("utf-8")


def _flatten(record: dict, prefix: str = "", sep: str = ".") -> dict:
    """Рекурсивно разворачивает вложенные dict в плоские поля через точку."""
    out: dict = {}
    for k, v in record.items():
        key = f"{prefix}{sep}{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key, sep))
        else:
            out[key] = v
    return out


def _meta() -> dict:
    return {
        "logzilla_version": __version__,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def export(records: list[dict], opts: ExportOptions) -> tuple[bytes, str, str]:
    """Сериализует записи в JSON. Возвращает (payload, mime, filename_suffix).

    ndjson: первой строкой — мета-объект с версией, далее по объекту на строку.
    json:   обёртка {"_logzilla": {...}, "records": [...]} — валидный JSON с мета.
    """
    if opts.flatten:
        records = [_flatten(r) for r in records]

    if opts.ndjson:
        lines = [_dumps({"_logzilla": _meta()})]
        lines += [_dumps(rec) for rec in records]
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        mime, ext = "application/x-ndjson", "ndjson"
    else:
        out = {"_logzilla": _meta(), "records": records}
        payload = _dumps_pretty(out) + b"\n"
        mime, ext = "application/json", "json"
    if opts.gzip:
        payload = gzip.compress(payload)
        mime = "application/gzip"
        ext = ext + ".gz"
    return payload, mime, ext
