"""Тесты ParseService: метрики, единый тип, диагностика, детерминизм."""

from pathlib import Path

from app.contract import ParseRequest
from app.parse_service import parse

FIXTURES = Path(__file__).parent / "fixtures"
SYSLOG = (FIXTURES / "syslog_sample.log").read_text(encoding="utf-8")


def _inline(text, **opts):
    return ParseRequest.from_dict({
        "source": {"kind": "inline", "text": text},
        "options": opts,
    })


def test_parse_inline_basic_metrics():
    res = parse(_inline(SYSLOG))
    assert res.status == "ok"
    assert res.metrics.total_lines == 7          # 7 непустых строк
    assert res.metrics.filtered == 7             # все записи прошли
    assert res.metrics.errors == 2               # 2 строки ERROR
    assert res.metrics.warnings == 2             # 2 строки WARN
    assert isinstance(res.records, list)         # единый тип


def test_metrics_have_duration_and_run_id():
    res = parse(_inline(SYSLOG))
    assert res.metrics.duration_ms >= 0
    assert res.run_id.startswith("r-")


def test_level_filter_reduces_records():
    res = parse(_inline(SYSLOG, log_levels=["ERROR"]))
    # После фильтра по ERROR остаются только строки с ERROR.
    assert res.metrics.filtered <= 7
    assert res.metrics.errors == res.metrics.filtered
    assert res.metrics.warnings == 0


def test_empty_input_produces_diagnostic():
    res = parse(_inline("   \n  \n"))
    codes = {d.code for d in res.diagnostics}
    assert "NO_RECORDS" in codes
    assert res.metrics.filtered == 0


def test_format_detected_diagnostic_present():
    res = parse(_inline(SYSLOG))
    codes = {d.code for d in res.diagnostics}
    assert "FORMAT_DETECTED" in codes
    assert res.format_detected is not None


def test_elastic_csv_with_at_timestamp_detected_and_expanded():
    """Kibana/Elastic-экспорт: первая колонка `@timestamp` (ведущий @) раньше
    проваливал детектор имён колонок → весь CSV уезжал в текстовый путь и message
    с вложенным JSON не раскрывался. Регресс на разбор по колонкам + expand_message."""
    csv_text = (
        '"@timestamp","env","message"\n'
        '"Jun 5, 2026 @ 20:29:02.565","preprod","{""level"":""INFO"",""method"":""GET"",'
        '""url"":""/api/system/about"",""status"":200,""req_id"":""7FDA"",""msg"":""Finish""}"\n'
        '"Jun 5, 2026 @ 20:29:02.566","preprod","{""level"":""DEBUG"",""sql"":""select 1"",'
        '""req_id"":""7FDA"",""msg"":""Query""}"'
    )
    res = parse(_inline(csv_text, expand_message=True))
    assert res.format_detected == "csv"
    # message раскрыт: структурные поля стали колонками записи
    rec = res.records[0]
    assert rec.get("method") == "GET"
    assert rec.get("url") == "/api/system/about"
    assert rec.get("status") == 200
    # SQL-запись тоже разобрана
    assert any(r.get("sql") for r in res.records)


def test_determinism_same_input_same_output():
    a = parse(_inline(SYSLOG)).to_dict()
    b = parse(_inline(SYSLOG)).to_dict()
    a.pop("run_id"); b.pop("run_id")
    a["metrics"].pop("duration_ms"); b["metrics"].pop("duration_ms")
    assert a == b                                # детерминизм ядра


def test_file_source(tmp_path):
    f = tmp_path / "s.log"
    f.write_text(SYSLOG, encoding="utf-8")
    res = parse(ParseRequest.from_dict({"source": {"kind": "file", "path": str(f)}}))
    assert res.status == "ok"
    assert res.metrics.filtered == 7
