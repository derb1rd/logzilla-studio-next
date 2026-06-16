"""ExportService — сериализация результата парсинга в JSON (+ опциональный gzip).

Раньше поддерживались json/csv/txt/xml; по решению заказчика выгрузка всегда JSON
(прочие форматы не нужны). gzip — опциональное сжатие поверх JSON.
"""

from __future__ import annotations

import gzip
import json

from .contract import ExportOptions


def _dumps(obj, **kw) -> str:
    """json.dumps с отступлением на ensure_ascii=True при одиночных суррогатах
    (битый/двойно-экранированный ввод иначе роняет кодирование в utf-8)."""
    try:
        return json.dumps(obj, ensure_ascii=False, **kw)
    except UnicodeEncodeError:
        return json.dumps(obj, ensure_ascii=True, **kw)


def export(records: list[dict], opts: ExportOptions) -> tuple[bytes, str, str]:
    """Сериализует записи в JSON. Возвращает (payload, mime, filename_suffix).

    ndjson: по объекту на строку (NDJSON/JSON Lines) — каждую запись видно
    отдельной строкой, удобнее грепать/диффать, чем один отступованный массив.
    """
    if opts.ndjson:
        text = "\n".join(_dumps(rec) for rec in records)
        payload = (text + "\n").encode("utf-8")
        mime, ext = "application/x-ndjson", "ndjson"
    else:
        payload = (_dumps(records, indent=2) + "\n").encode("utf-8")
        mime, ext = "application/json", "json"
    if opts.gzip:
        payload = gzip.compress(payload)
        mime = "application/gzip"
        ext = ext + ".gz"
    return payload, mime, ext
