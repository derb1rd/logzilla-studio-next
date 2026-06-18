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


def _dumps(obj, **kw) -> str:
    """json.dumps с отступлением на ensure_ascii=True при одиночных суррогатах
    (битый/двойно-экранированный ввод иначе роняет кодирование в utf-8)."""
    try:
        return json.dumps(obj, ensure_ascii=False, **kw)
    except UnicodeEncodeError:
        return json.dumps(obj, ensure_ascii=True, **kw)


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
        payload = (_dumps(out, indent=2) + "\n").encode("utf-8")
        mime, ext = "application/json", "json"
    if opts.gzip:
        payload = gzip.compress(payload)
        mime = "application/gzip"
        ext = ext + ".gz"
    return payload, mime, ext
