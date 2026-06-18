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


def test_kibana_csv_warning_with_commas_quotes_parens():
    """Реальная WARNING-запись клиента: message c запятыми, одинарными кавычками,
    скобками и datetime(...) внутри. Должна разобраться как csv и раскрыться в
    структуру (level=WARNING, читаемый текст), а не уехать в текстовый путь, где
    эвристики выдают мусор (http_status=['288','148','047'], json_snippet, raw)."""
    msg = (
        "ВР 9584 находится в статусе error! Запрос UpdateRuleStatus(disclosure_id=9584, "
        "calculation_id=148, org_unit='198349', rule_id=5397, step=, "
        "reason='Расчет ВР в статусе error.', time=datetime.datetime(1970, 1, 1, 0, 0), "
        "headers=None) не будет обработан."
    )
    csv_text = (
        '"@timestamp","env","message"\n'
        '"Jun 5, 2026 @ 19:51:55.288","preprod","{""level"": ""WARNING"", ""message"": ""'
        + msg + '"", ""service_name"": ""drills""}"'
    )
    res = parse(_inline(csv_text, expand_message=True))
    assert res.format_detected == "csv"
    rec = res.records[0]
    assert rec.get("level") == "WARNING"
    assert rec.get("message") == msg          # человеческий текст, не мусор
    assert rec.get("service_name") == "drills"
    # признаки текстового пути ОТСУТСТВУЮТ
    assert "json_snippet" not in rec and "http_status" not in rec and "raw" not in rec


def test_csv_with_multiline_field_detected_and_preserved():
    """Поле message с сырым переводом строки (трассировка) в первых строках раньше
    рвало наивный split('\\n') в детекторе → файл уезжал в мусорный текст-путь.
    csv.reader-детекция склеивает многострочное поле: формат csv, все записи целы."""
    csv_text = (
        '"@timestamp","env","message"\n'
        '"Jun 5 @ 19:00","preprod","{""level"": ""ERROR"", ""trace"": ""Traceback:\n'
        '  File a, line 1\n  File b, line 2""}"\n'
        '"Jun 5 @ 19:01","preprod","{""level"": ""INFO"", ""msg"": ""after""}"'
    )
    res = parse(_inline(csv_text, expand_message=True))
    assert res.format_detected == "csv"
    assert len(res.records) == 2               # запись с трейсом не потеряна
    # вторая запись раскрылась штатно
    assert res.records[1].get("level") == "INFO"
    # признаков текстового пути нет ни в одной записи
    assert all("json_snippet" not in r and "raw" not in r for r in res.records)


def test_jsonl_level_filter_applied():
    """Регресс: фильтр по уровню для JSONL раньше молча игнорировался (фильтрация
    шла по сырым строкам только для текст/CSV). Теперь фильтр — на уровне записей и
    работает для JSON/JSONL тоже."""
    jsonl = (
        '{"level":"INFO","msg":"a","ts":"2025-01-15T10:00:00Z"}\n'
        '{"level":"ERROR","msg":"b","ts":"2025-01-15T11:00:00Z"}\n'
        '{"level":"WARN","msg":"c","ts":"2025-01-15T12:00:00Z"}'
    )
    res = parse(_inline(jsonl, log_levels=["ERROR"]))
    assert res.format_detected == "jsonl"
    assert res.metrics.filtered == 1
    assert res.records[0]["level"] == "ERROR"


def test_jsonl_date_filter_applied():
    """Дата-фильтр работает на JSONL через канонический _ts (раньше — недоступен)."""
    jsonl = (
        '{"level":"INFO","msg":"a","ts":"2025-01-15T10:00:00Z"}\n'
        '{"level":"INFO","msg":"b","ts":"2025-01-16T10:00:00Z"}'
    )
    res = parse(_inline(jsonl, date_start="2025-01-16"))
    assert res.metrics.filtered == 1
    assert res.records[0]["msg"] == "b"
    # канонический timestamp проставлен
    assert "_ts" in res.records[0] and "_ts_iso" in res.records[0]


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
