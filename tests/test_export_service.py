"""Тесты ExportService: JSON + gzip-декоратор (выгрузка всегда JSON)."""

import gzip
import json

from app.contract import ExportOptions
from app.export_service import export

RECORDS = [
    {"level": "ERROR", "msg": "boom", "code": 500},
    {"level": "INFO", "msg": "ok", "raw": "2024 INFO ok"},
]


def test_json_export():
    payload, mime, ext = export(RECORDS, ExportOptions())
    assert mime == "application/json"
    assert ext == "json"
    assert json.loads(payload.decode("utf-8")) == RECORDS


def test_gzip_decorator():
    payload, mime, ext = export(RECORDS, ExportOptions(gzip=True))
    assert mime == "application/gzip"
    assert ext == "json.gz"
    assert json.loads(gzip.decompress(payload).decode("utf-8")) == RECORDS
