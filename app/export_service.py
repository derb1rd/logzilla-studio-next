"""ExportService — сериализация результата парсинга в JSON (+ опциональный gzip).

Раньше поддерживались json/csv/txt/xml; по решению заказчика выгрузка всегда JSON
(прочие форматы не нужны). gzip — опциональное сжатие поверх JSON.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

from . import __version__
from .contract import ExportOptions

try:
    from logZilla3000.sql_formatter import unescape_sql_in_json as _unescape_sql
except ImportError:
    _unescape_sql = None  # type: ignore[assignment]


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


def _dumps_pretty(obj) -> bytes:
    """JSON (indent=2) в utf-8 bytes с реальными переносами строк в SQL-полях.

    Применяет ту же постобработку что CLI (unescape_sql_in_json): sql/query/statement
    получают настоящие \\n вместо escape-последовательностей — файл читабелен.

    Тонкость с суррогатами: unescape_sql_in_json round-trip'ит через json.loads/
    dumps и может вернуть одиночный суррогат (битый ввод) обратно живым символом —
    тогда .encode('utf-8') упал бы (защита _dumps к этому моменту уже снята). В этом
    редком случае откатываемся на безопасную ensure_ascii-форму БЕЗ SQL-переносов:
    целостность выгрузки важнее косметики. Возвращаем bytes, чтобы кодировать ровно
    один раз — в той точке, где и срабатывает fallback.
    """
    raw = _dumps(obj, indent=2)                 # str, гарантированно кодируемая в utf-8
    if _unescape_sql is not None:
        cooked = _unescape_sql(raw)
        try:
            return cooked.encode("utf-8")       # SQL-переносы + utf-8
        except UnicodeEncodeError:
            pass                                # постобработка вернула суррогат → откат
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
