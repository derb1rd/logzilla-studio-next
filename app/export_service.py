"""ExportService — сериализация результата парсинга в JSON (+ опциональный gzip).

Раньше поддерживались json/csv/txt/xml; по решению заказчика выгрузка всегда JSON
(прочие форматы не нужны). gzip — опциональное сжатие поверх JSON.
"""

from __future__ import annotations

import gzip
import json

from .contract import ExportOptions


def export(records: list[dict], opts: ExportOptions) -> tuple[bytes, str, str]:
    """Сериализует записи в JSON. Возвращает (payload, mime, filename_suffix)."""
    try:
        text = json.dumps(records, ensure_ascii=False, indent=2)
        payload = (text + "\n").encode("utf-8")
    except UnicodeEncodeError:
        # Одиночные суррогаты (битый ввод) не кодируются в utf-8 — отступаем на
        # ensure_ascii=True (экранирует их как \\udXXX), чтобы экспорт не падал.
        payload = (json.dumps(records, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    mime, ext = "application/json", "json"
    if opts.gzip:
        payload = gzip.compress(payload)
        mime = "application/gzip"
        ext = ext + ".gz"
    return payload, mime, ext
