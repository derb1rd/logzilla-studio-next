"""Smoke-проверка без pytest: python3 tests/smoke.py

Прогоняет контракт, ParseService и ExportService на эталонной фикстуре
и печатает результат. Возвращает код 0 при успехе.
"""

import sys
from pathlib import Path

# bootstrap путей: делаем импортируемым `app` (ядро подключит app/__init__.py)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.contract import ExportOptions, ExportRequest, ParseRequest  # noqa: E402
from app.export_service import export  # noqa: E402
from app.parse_service import parse, parse_all_records  # noqa: E402

SYSLOG = (_ROOT / "tests" / "fixtures" / "syslog_sample.log").read_text(encoding="utf-8")


def check(name: str, cond: bool) -> None:
    print(f"  {'✓' if cond else '✗'} {name}")
    if not cond:
        raise AssertionError(name)


def main() -> int:
    print("contract:")
    req = ParseRequest.from_dict({
        "source": {"kind": "inline", "text": SYSLOG},
        "options": {"log_levels": ["error", "warn"]},
    })
    check("log_levels normalized", req.options.log_levels == ["ERROR", "WARN"])

    print("parse_service:")
    res = parse(ParseRequest.from_dict({"source": {"kind": "inline", "text": SYSLOG}}))
    check("status ok", res.status == "ok")
    check("total_lines == 7", res.metrics.total_lines == 7)
    check("errors == 2", res.metrics.errors == 2)
    check("warnings == 2", res.metrics.warnings == 2)
    check("records is list", isinstance(res.records, list))
    check("run_id present", res.run_id.startswith("r-"))

    print("export_service:")
    full, truncated, total = parse_all_records(ParseRequest.from_dict({"source": {"kind": "inline", "text": SYSLOG}}))
    payload, mime, ext = export(full, ExportOptions())
    check(f"json non-empty ({len(payload)}B, {mime})", len(payload) > 0 and ext == "json")
    gz, mime, ext = export(full, ExportOptions(gzip=True))
    check("gzip ext json.gz", ext == "json.gz")

    print("\nALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
