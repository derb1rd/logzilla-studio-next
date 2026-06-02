"""Тесты контракта: валидация ParseRequest/ExportRequest и round-trip."""

import pytest

from app.contract import (
    ContractError,
    ExportRequest,
    ParseOptions,
    ParseRequest,
)


def test_parse_request_minimal_inline():
    req = ParseRequest.from_dict({"source": {"kind": "inline", "text": "hello"}})
    assert req.source.kind == "inline"
    assert req.source.text == "hello"
    assert req.options.encoding == "utf-8"      # дефолт
    assert req.preview.limit == 10000            # дефолт (= PREVIEW_MAX)


def test_parse_request_roundtrip():
    payload = {
        "version": "1",
        "source": {"kind": "inline", "text": "x"},
        "options": {"log_levels": ["error", "warn"], "compact_json": True},
        "preview": {"limit": 50, "offset": 10},
    }
    req = ParseRequest.from_dict(payload)
    assert req.options.log_levels == ["ERROR", "WARN"]   # нормализованы в верхний регистр
    assert req.options.compact_json is True
    out = req.to_dict()
    assert out["options"]["log_levels"] == ["ERROR", "WARN"]
    assert out["preview"] == {"limit": 50, "offset": 10}


def test_inline_requires_text():
    with pytest.raises(ContractError):
        ParseRequest.from_dict({"source": {"kind": "inline"}})


def test_file_requires_path():
    with pytest.raises(ContractError):
        ParseRequest.from_dict({"source": {"kind": "file"}})


def test_bad_source_kind():
    with pytest.raises(ContractError):
        ParseRequest.from_dict({"source": {"kind": "ftp", "path": "/x"}})


def test_bad_log_levels_type():
    with pytest.raises(ContractError):
        ParseOptions.from_dict({"log_levels": "ERROR"})


def test_negative_offset_rejected():
    with pytest.raises(ContractError):
        ParseRequest.from_dict({"source": {"kind": "inline", "text": "x"}, "preview": {"offset": -1}})


def test_preview_limit_capped_at_max():
    # Окно предпросмотра зажимается до PREVIEW_MAX (10000): запрос на 50000 → 10000.
    req = ParseRequest.from_dict({
        "source": {"kind": "inline", "text": "x"},
        "preview": {"limit": 50000},
    })
    assert req.preview.limit == 10000


def test_export_request_json_only():
    # Выгрузка всегда JSON: формат не указывается, gzip — опционально.
    req = ExportRequest.from_dict({
        "parse_request": {"source": {"kind": "inline", "text": "x"}},
        "options": {"gzip": True},
    })
    assert req.parse_request.source.kind == "inline"
    assert req.options.gzip is True


def test_export_request_defaults_no_gzip():
    req = ExportRequest.from_dict({"parse_request": {"source": {"kind": "inline", "text": "x"}}})
    assert req.options.gzip is False
