"""ParseService — тонкий сервисный слой поверх ядра logZilla3000.

Делает три вещи, которых нет в ядре, но требует контракт:
  1. Нормализует результат к ЕДИНОМУ типу list[dict] на границе, не трогая ядро.
  2. Собирает MetricsCollector → metrics (total/filtered/errors/warnings/duration).
  3. Наполняет diagnostics[] (формат, кодировка, пустой результат, обрезка).

Ядро остаётся детерминированным и нетронутым.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import NamedTuple
from uuid import uuid4

from logZilla3000 import UniversalLogParser  # ядро (через bootstrap в app/__init__.py)
from logZilla3000.detectors import FormatDetector

from .product_filter import filter_records as _product_filter
from .contract import (
    MAX_RECORDS,
    CONTRACT_VERSION,
    Diagnostic,
    Metrics,
    ParseRequest,
    ParseResult,
)
from .logging_setup import log_event

logger = logging.getLogger("studio.parse")

_LEVEL_RE = re.compile(
    r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|TRACE)\b", re.IGNORECASE
)
_LEVEL_KEYS = ("level", "levelname", "log_level", "loglevel", "severity", "lvl")
# Текстовые поля строки лога — для логов без явного поля уровня (напр. plain-text → "raw").
_TEXT_KEYS = ("message", "msg", "raw", "line", "text", "log")
_ERROR_LEVELS = {"ERROR", "FATAL", "CRITICAL"}
_WARN_LEVELS = {"WARN", "WARNING"}

# Кодировки для fallback при чтении файла (mirror логики ядра parser._read_file).
_FALLBACK_ENCODINGS = ("utf-8-sig", "cp1251", "koi8-r", "latin-1")


def _level_token(value: str) -> str | None:
    m = _LEVEL_RE.search(value)
    if not m:
        return None
    lvl = m.group(1).upper()
    return "WARN" if lvl == "WARNING" else lvl


# Числовой уровень → имя (зеркало web/core.js levelFromNumber): RFC5424/GELF
# severity (0–7) и pino/bunyan level (10–60). Диапазон разводит две шкалы.
_SYSLOG_SEVERITY = ("FATAL", "FATAL", "CRITICAL", "ERROR", "WARN", "INFO", "INFO", "DEBUG")
_PINO_LEVELS = {10: "TRACE", 20: "DEBUG", 30: "INFO", 40: "WARN", 50: "ERROR", 60: "FATAL"}


def _level_from_number(n: object) -> str | None:
    if not isinstance(n, int) or isinstance(n, bool):
        return None
    if 0 <= n <= 7:
        return _SYSLOG_SEVERITY[n]
    if 10 <= n <= 60:
        return _PINO_LEVELS.get(min(60, round(n / 10) * 10))
    return None


def _record_level(record: dict) -> str | None:
    """Определяет уровень записи: сначала по явному полю уровня, затем по тексту строки лога.

    MED-2: раньше fallback искал уровень во ВСЁМ json.dumps(record), из-за чего слова
    ERROR/WARN в произвольных полях/значениях ложно завышали счётчики. Теперь fallback
    ограничен текстовыми полями (_TEXT_KEYS) и берёт первый уровневый токен — как уровень
    в начале строки лога.
    """
    for key in _LEVEL_KEYS:
        v = record.get(key)
        if isinstance(v, str):
            lvl = _level_token(v)
            if lvl:
                return lvl
        elif isinstance(v, int) and not isinstance(v, bool):
            lvl = _level_from_number(v)
            if lvl:
                return lvl
    for key in _TEXT_KEYS:
        v = record.get(key)
        if isinstance(v, str):
            lvl = _level_token(v)
            if lvl:
                return lvl
    return None


def _count_levels(records: list[dict]) -> tuple[int, int]:
    errors = warnings = 0
    for rec in records:
        lvl = _record_level(rec)
        if lvl in _ERROR_LEVELS:
            errors += 1
        elif lvl in _WARN_LEVELS:
            warnings += 1
    return errors, warnings


def _normalize(result: object) -> list[dict]:
    """Единый тип возврата: всегда list[dict]."""
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        return [result]
    return [{"message": str(result)}]


def _read_file(path: str, encoding: str) -> tuple[str, Diagnostic | None]:
    encodings = [encoding] + [e for e in _FALLBACK_ENCODINGS if e != encoding]
    last_exc: Exception | None = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            diag = None
            if enc != encoding:
                diag = Diagnostic(
                    "warn", "ENCODING_FALLBACK",
                    f"Кодировка '{encoding}' не подошла, использована '{enc}'",
                )
            return text, diag
        except UnicodeDecodeError as e:
            last_exc = e
            continue
    raise last_exc or UnicodeDecodeError("?", b"", 0, 1, "не удалось декодировать файл")


def _detect_format(raw: str) -> str:
    try:
        return FormatDetector().detect(raw).value
    except Exception:  # детектор не должен ронять прогон
        return "unknown"


def _resolve_format(req: ParseRequest, raw: str) -> str:
    """Формат, фактически применённый ядром (MED-3).

    Для file-источника ядро выбирает формат по расширению (parse_file/parse_text),
    а не по содержимому — поэтому для data.csv нельзя пере-детектить и показывать
    "text". Переиспользуем ту же таблицу расширений, что и ядро
    (UniversalLogParser.FORMAT_BY_EXT), чтобы правила не разъезжались; иначе (и для
    inline) — автоопределение по содержимому, как делает parser.parse().
    """
    if req.source.kind == "file":
        ext = os.path.splitext(req.source.path or "")[1].lower()
        fmt = UniversalLogParser.FORMAT_BY_EXT.get(ext)
        if fmt is not None:
            return fmt.value
    return _detect_format(raw)


def _fingerprint(raw: str) -> str:
    """Короткий sha1 ввода — для воспроизводимости без логирования содержимого (PII-safe)."""
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def _build_parser(req: ParseRequest) -> UniversalLogParser:
    o = req.options
    return UniversalLogParser(
        encoding=o.encoding,
        log_levels=o.log_levels or None,
        remove_ansi=o.remove_ansi,
        remove_duplicates=o.remove_duplicates,
        expand_message=o.expand_message,
        format_sql=o.format_sql,
        strip_k8s=o.strip_k8s,
        indent=None if o.compact_json else 2,
    )


class _Run(NamedTuple):
    """Результат единого пути парсинга (общего для предпросмотра и экспорта)."""
    records: list[dict]            # уже после применения капа MAX_RECORDS
    raw: str
    fmt: str
    diagnostics: list[Diagnostic]
    truncated: bool
    total: int                     # число записей ДО обрезки


def _run(req: ParseRequest) -> _Run:
    """Единый внутренний путь: чтение → детекция формата → ядро → нормализация →
    обрезка с ЯВНЫМ сигналом.

    Поверх него строятся И предпросмотр (берёт окно), И экспорт (берёт весь набор).
    Раньше это были два разошедшихся пути, из-за чего экспорт молча терял данные
    сверх MAX_RECORDS (HIGH-1) и парсил источник повторно (MED-1). Бросает
    исключение при ошибке чтения/парсинга — обрабатывает вызывающий.
    """
    diagnostics: list[Diagnostic] = []

    if req.source.kind == "inline":
        raw = req.source.text or ""
    else:
        # Читаем файл ОДИН раз: raw нужен для метрик/диагностики кодировки, и он же
        # уходит в parse_text — ядро повторно файл не открывает (раньше parse_file
        # читал его второй раз). filepath передаём только ради выбора формата по
        # расширению.
        raw, diag = _read_file(req.source.path or "", req.options.encoding)
        if diag:
            diagnostics.append(diag)

    fmt = _resolve_format(req, raw)
    parser = _build_parser(req)
    if req.source.kind == "file":
        result = parser.parse_text(raw, filepath=req.source.path)
    else:
        result = parser.parse(raw)
    records = _normalize(result)
    if req.options.product_filter:
        records = _product_filter(records)

    total = len(records)
    truncated = False
    if total > MAX_RECORDS:
        truncated = True
        diagnostics.append(Diagnostic(
            "warn", "TRUNCATED",
            f"Результат обрезан до {MAX_RECORDS} записей (всего {total})",
        ))
        records = records[:MAX_RECORDS]

    return _Run(records, raw, fmt, diagnostics, truncated, total)


def parse(req: ParseRequest, correlation_id: str | None = None) -> ParseResult:
    """Главная операция сервиса: ParseRequest → ParseResult (детерминированно).

    correlation_id связывает прогон с action log клиента:
    client X-Correlation-Id ↔ server run_id.
    """
    run_id = "r-" + uuid4().hex[:8]
    t0 = time.perf_counter()

    log_event(
        logger, "parse_started", run_id=run_id, correlation_id=correlation_id,
        source_kind=req.source.kind,
        log_levels=req.options.log_levels,
        compact=req.options.compact_json,
    )

    try:
        run = _run(req)
    except Exception as e:  # noqa: BLE001 — граница: любое чтение/парс-исключение → error-результат
        log_event(logger, "parse_failed", level=logging.ERROR, run_id=run_id, error=str(e))
        return _error_result(run_id, t0, e, [])

    records = run.records
    status = "partial" if run.truncated else "ok"
    diagnostics = list(run.diagnostics)

    # Метрики.
    total_lines = sum(1 for ln in run.raw.splitlines() if ln.strip())
    errors, warnings = _count_levels(records)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    metrics = Metrics(
        total_lines=total_lines,
        filtered=len(records),
        errors=errors,
        warnings=warnings,
        duration_ms=duration_ms,
    )

    # Diagnostics + окно предпросмотра.
    diagnostics.insert(0, Diagnostic("info", "FORMAT_DETECTED", f"Определён формат: {run.fmt}"))
    if not records:
        diagnostics.append(Diagnostic("warn", "NO_RECORDS", "Парсер не извлёк ни одной записи"))

    offset, limit = req.preview.offset, req.preview.limit
    page = records[offset:offset + limit]
    preview_window = {
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "total_records": len(records),
        "has_more": offset + limit < len(records),
        "truncated": run.truncated,
    }

    log_event(
        logger, "parse_done", run_id=run_id, correlation_id=correlation_id,
        status=status, format=run.fmt,
        input_len=len(run.raw), input_sha1=_fingerprint(run.raw),
        total_lines=total_lines, filtered=len(records),
        errors=errors, warnings=warnings, duration_ms=duration_ms,
    )

    return ParseResult(
        status=status,
        format_detected=run.fmt,
        metrics=metrics,
        records=page,
        preview_window=preview_window,
        diagnostics=diagnostics,
        run_id=run_id,
        version=CONTRACT_VERSION,
    )


def parse_all_records(req: ParseRequest) -> tuple[list[dict], bool, int]:
    """Полный набор записей для экспорта + сигнал обрезки.

    Возвращает (records, truncated, total). Делит общий путь _run() с
    предпросмотром, поэтому обрезка сверх MAX_RECORDS больше не молчаливая (HIGH-1):
    сервер доносит её до пользователя заголовками ответа.
    """
    run = _run(req)
    return run.records, run.truncated, run.total


def _error_result(
    run_id: str, t0: float, exc: Exception,
    diagnostics: list[Diagnostic], fmt: str | None = None,
) -> ParseResult:
    diagnostics.append(Diagnostic("error", type(exc).__name__, str(exc)))
    return ParseResult(
        status="error",
        format_detected=fmt,
        metrics=Metrics(0, 0, 0, 0, int((time.perf_counter() - t0) * 1000)),
        records=[],
        preview_window={"offset": 0, "limit": 0, "returned": 0,
                        "total_records": 0, "has_more": False, "truncated": False},
        diagnostics=diagnostics,
        run_id=run_id,
        version=CONTRACT_VERSION,
    )
