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
        "Задача 9584 находится в статусе error! Запрос UpdateStatus(task_id=9584, "
        "batch_id=148, unit='198349', rule_id=5397, step=, "
        "reason='Расчет задачи в статусе error.', time=datetime.datetime(1970, 1, 1, 0, 0), "
        "headers=None) не будет обработан."
    )
    csv_text = (
        '"@timestamp","env","message"\n'
        '"Jun 5, 2026 @ 19:51:55.288","preprod","{""level"": ""WARNING"", ""message"": ""'
        + msg + '"", ""service_name"": ""worker""}"'
    )
    res = parse(_inline(csv_text, expand_message=True))
    assert res.format_detected == "csv"
    rec = res.records[0]
    assert rec.get("level") == "WARNING"
    assert rec.get("message") == msg          # человеческий текст, не мусор
    assert rec.get("service_name") == "worker"
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


def test_python_traceback_grouped_into_one_record():
    """Полный Python traceback (с Flask-заголовком) должен давать 1 запись,
    а не по записи на каждую строку. Поля type и message извлекаются из конца."""
    tb = (
        "[2026-05-12 05:47:01,957] ERROR in app: Exception on /api/editor/v1/schema [PUT]\n"
        "Traceback (most recent call last):\n"
        "  File \"/opt/application/venv/lib/python3.12/site-packages/flask/app.py\", line 1823, in full_dispatch_request\n"
        "    rv = self.dispatch_request()\n"
        "  File \"/opt/application/venv/lib/python3.12/site-packages/ext_client/modules/mixins.py\", line 56, in build_request\n"
        "    return AuditRequest()\n"
        "TypeError: bad argument type for built-in operation"
    )
    res = parse(_inline(tb))
    assert res.format_detected == "python_traceback"
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.get("level") == "ERROR"
    assert rec.get("type") == "TypeError"
    assert rec.get("message") == "bad argument type for built-in operation"
    assert rec.get("url") == "/api/editor/v1/schema"
    assert rec.get("method") == "PUT"
    assert isinstance(rec.get("frames"), list) and len(rec["frames"]) >= 2
    assert "traceback" in rec


def test_python_traceback_without_header():
    """Чистый traceback без Flask-заголовка — тоже 1 запись."""
    tb = (
        "Traceback (most recent call last):\n"
        "  File \"/opt/application/venv/lib/python3.12/site-packages/ext_client/sync.py\", line 24, in __exit__\n"
        "    self.write_logs(self.messages)\n"
        "psycopg2.OperationalError: SSL connection has been closed unexpectedly"
    )
    res = parse(_inline(tb))
    assert res.format_detected == "python_traceback"
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.get("type") == "psycopg2.OperationalError"
    assert "SSL connection" in rec.get("message", "")


def test_go_goroutine_dump_grouped():
    """Горутин-дамп Go (goroutine N [running]:) должен давать 1 запись с level=FATAL."""
    dump = (
        "goroutine 20928 [running]:\n"
        "net/http.(*conn).serve.func1()\n"
        "\t/usr/local/go/src/net/http/server.go:1947 +0xbe\n"
        "panic({0x222f600?, 0x4959500?})\n"
        "\t/usr/local/go/src/runtime/panic.go:792 +0x132\n"
        "main.(*Server).handleRequest(0xc000b5a000)\n"
        "\t/app/server.go:142 +0x3a8"
    )
    res = parse(_inline(dump))
    assert res.format_detected == "go_panic"
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.get("level") == "FATAL"
    assert rec.get("goroutine") == 20928
    assert "traceback" in rec


def test_go_http_panic_extracts_message():
    """'http: panic serving IP:port: runtime error:...' + горутин-дамп — 1 запись
    с type=panic и message=сообщение об ошибке."""
    dump = (
        "2026/03/27 09:34:01 http: panic serving 10.100.13.180:34844: "
        "runtime error: invalid memory address or nil pointer dereference\n"
        "goroutine 11243 [running]:\n"
        "net/http.(*conn).serve.func1()\n"
        "\t/usr/local/go/src/net/http/server.go:1947 +0xbe"
    )
    res = parse(_inline(dump))
    assert res.format_detected == "go_panic"
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.get("type") == "panic"
    assert "nil pointer dereference" in rec.get("message", "")


def test_exception_group_grouped():
    """ExceptionGroup / anyio TaskGroup должен давать 1 запись с sub_exceptions."""
    eg = (
        "ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)\n"
        "  + Exception Group Traceback (most recent call last):\n"
        "  |   File \"/opt/application/src/core/engine.py\", line 54, in __init__\n"
        "  |     self._parser = self._pg.build()\n"
        "  | PermissionError: [Errno 13] Permission denied: '/home/reasop'\n"
        "  +------------------------------------"
    )
    res = parse(_inline(eg))
    assert res.format_detected == "exception_group"
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.get("type") == "ExceptionGroup"
    assert isinstance(rec.get("sub_exceptions"), list)
    assert rec["sub_exceptions"][0]["type"] == "PermissionError"


def test_single_exception_line_is_text_not_traceback():
    """Единственная строка исключения без заголовка — просто text-сообщение, не traceback."""
    res = parse(_inline("TypeError: bad argument type for built-in operation"))
    assert res.format_detected != "python_traceback"
    assert len(res.records) == 1
    assert res.records[0].get("message") == "TypeError: bad argument type for built-in operation"


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
