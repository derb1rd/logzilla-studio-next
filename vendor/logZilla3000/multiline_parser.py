"""Multi-line log format parsers.

Groups Python tracebacks, Go goroutine dumps and ExceptionGroups from
raw pasted text into single structured records, instead of one-per-line.
"""

from __future__ import annotations

import re
from typing import Any

# ── Detection patterns ─────────────────────────────────────────────────────

# Python "Traceback (most recent call last):" — universal marker.
_PY_TB_HEADER = re.compile(r"Traceback \(most recent call last\):", re.MULTILINE)

# Flask / gunicorn / uvicorn error handler prefix that precedes a traceback:
#   [2026-05-12 05:47:01,957] ERROR in app: Exception on /api/... [PUT]
_FLASK_HEADER = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+ERROR\s+in\s+\w+:\s+Exception\s+on\s+"
    r"(?P<url>\S+)\s+\[(?P<method>[A-Z]+)\]",
    re.MULTILINE,
)

# Go goroutine dump: "goroutine N [state]:" — the structural anchor.
_GO_GOROUTINE = re.compile(r"goroutine\s+(?P<id>\d+)\s+\[(?P<state>[^\]]+)\]:", re.MULTILINE)

# Go panic line: "panic: ..." / "http: panic serving IP:port: ..." / "YYYY/MM/DD ... panic ..."
# IP:port in serving address (e.g. 10.100.13.180:34844) needs \S+ to consume past the colon.
_GO_PANIC_LINE = re.compile(
    r"(?:^|\n)"
    r"(?:\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+)?"   # optional timestamp
    r"(?:http:\s+)?"                                       # optional "http: "
    r"panic(?:\s+serving\s+\S+)?:\s+"                     # "panic:" or "panic serving IP:port:"
    r"(?:runtime error:\s+)?"                             # optional "runtime error: "
    r"(?P<msg>[^\n]+)",
    re.MULTILINE,
)

# ExceptionGroup anchor: "ExceptionGroup: ..." or "Exception Group Traceback"
_EG_HEADER = re.compile(
    r"ExceptionGroup:\s*(?P<msg>[^\n]+)|Exception Group Traceback",
    re.MULTILINE,
)

# Sub-exception inside ExceptionGroup: "| SomeError: message" or " SomeError: message"
_EG_SUBEXC = re.compile(
    r"[|+\s]+(?P<type>(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Warning|Exit|Interrupt|Group)):\s*(?P<msg>[^\n]+)",
    re.MULTILINE,
)

# ── Shared helpers ─────────────────────────────────────────────────────────

# Python stack frame: '  File "path", line N, in func_name'
_FRAME_RE = re.compile(
    r'File "(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<func>\S+)'
)

# Python exception final line: dotted.class.Name: message
# Only matches class names ending in common exception suffixes.
_EXC_SUFFIXES = re.compile(
    r"(?:Error|Exception|Warning|Exit|Interrupt|Group|Violation|Conflict|Failure|Timeout)$"
)
_EXC_LINE_RE = re.compile(
    r"^(?P<type>(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w+):\s*(?P<msg>.*)$"
)

# Go stack frame: "    /path/to/file.go:line +0xoffset" (tab-indented)
_GO_FRAME_RE = re.compile(
    r"^\s+(?P<file>[^\s(]+\.go):(?P<line>\d+)"
)


def _looks_like_exception_type(name: str) -> bool:
    last = name.rsplit(".", 1)[-1]
    return bool(_EXC_SUFFIXES.search(last)) or last in {
        "StopIteration", "GeneratorExit", "StopAsyncIteration", "BaseException"
    }


def _extract_py_exception(lines: list[str]) -> tuple[str | None, str | None]:
    """Find exception type and message: scan from the end for 'ExcType: msg'."""
    for line in reversed(lines):
        s = line.strip()
        if not s:
            continue
        m = _EXC_LINE_RE.match(s)
        if m and _looks_like_exception_type(m.group("type")):
            return m.group("type"), m.group("msg").strip()
    return None, None


def _extract_frames(text: str) -> list[dict]:
    frames = []
    for m in _FRAME_RE.finditer(text):
        frames.append({
            "file": m.group("file"),
            "line": int(m.group("line")),
            "function": m.group("func"),
        })
    return frames


# ── Public parsers ─────────────────────────────────────────────────────────

def parse_python_traceback(text: str) -> dict[str, Any]:
    """Parse a Python traceback block into a single structured record.

    Handles:
    - Pure traceback starting with "Traceback (most recent call last):"
    - Flask/gunicorn/uvicorn error header followed by the traceback
    """
    result: dict[str, Any] = {"level": "ERROR"}

    # Check for Flask error header
    fm = _FLASK_HEADER.search(text)
    if fm:
        result["timestamp"] = fm.group("ts").strip()
        result["url"] = fm.group("url")
        result["method"] = fm.group("method")

    # Extract exception type and message from end of text
    lines = text.splitlines()
    exc_type, exc_msg = _extract_py_exception(lines)
    if exc_type:
        result["type"] = exc_type
        result["message"] = exc_msg or ""
    else:
        # Fallback: last non-empty line
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("File ") and "Traceback" not in stripped:
                result["message"] = stripped
                break

    # Stack frames
    frames = _extract_frames(text)
    if frames:
        result["frames"] = frames

    result["traceback"] = text.strip()
    return result


def parse_go_panic(text: str) -> list[dict[str, Any]]:
    """Parse a Go goroutine dump into one record per goroutine.

    If there's a 'panic:' line, it's added to the first goroutine record.
    """
    # Find the panic message (may appear before or after goroutine dump)
    panic_msg: str | None = None
    pm = _GO_PANIC_LINE.search(text)
    if pm:
        panic_msg = pm.group("msg").strip()

    # Split text on goroutine boundaries
    goroutine_sections = _split_goroutines(text)
    if not goroutine_sections:
        # No goroutine markers — single record
        rec: dict[str, Any] = {"level": "FATAL", "traceback": text.strip()}
        if panic_msg:
            rec["type"] = "panic"
            rec["message"] = panic_msg
        return [rec]

    records = []
    for i, (gid, gstate, body) in enumerate(goroutine_sections):
        rec = {
            "level": "FATAL",
            "goroutine": gid,
            "goroutine_state": gstate,
            "traceback": body.strip(),
        }
        if i == 0 and panic_msg:
            rec["type"] = "panic"
            rec["message"] = panic_msg
        # Extract Go stack frames
        go_frames = []
        for m in _GO_FRAME_RE.finditer(body):
            go_frames.append({"file": m.group("file"), "line": int(m.group("line"))})
        if go_frames:
            rec["frames"] = go_frames
        records.append(rec)

    return records


def _split_goroutines(text: str) -> list[tuple[int, str, str]]:
    """Split goroutine dump text into (goroutine_id, state, body) tuples."""
    matches = list(_GO_GOROUTINE.finditer(text))
    if not matches:
        return []
    sections = []
    for i, m in enumerate(matches):
        gid = int(m.group("id"))
        gstate = m.group("state")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        sections.append((gid, gstate, body))
    return sections


def parse_exception_group(text: str) -> dict[str, Any]:
    """Parse a Python ExceptionGroup/anyio TaskGroup dump into a structured record."""
    result: dict[str, Any] = {"level": "ERROR", "type": "ExceptionGroup"}

    m = _EG_HEADER.search(text)
    if m and m.lastgroup == "msg":
        result["message"] = m.group("msg").strip()
    else:
        result["message"] = "unhandled errors in a TaskGroup"

    sub_exceptions = []
    for sm in _EG_SUBEXC.finditer(text):
        sub_exceptions.append({
            "type": sm.group("type"),
            "message": sm.group("msg").strip(),
        })
    if sub_exceptions:
        result["sub_exceptions"] = sub_exceptions

    frames = _extract_frames(text)
    if frames:
        result["frames"] = frames

    result["traceback"] = text.strip()
    return result


# ── Format detection helpers (used by detectors.py) ───────────────────────

def is_python_traceback(sample_lines: list[str], full_text: str) -> bool:
    """True when the text block is a Python traceback (complete or partial).

    Covers:
    - Full traceback with "Traceback (most recent call last):" header
    - Flask/gunicorn error header preceding a traceback
    - Stack frame fragments (≥2 lines starting with 'File "...' or '  File "...')
    """
    head = "\n".join(sample_lines[:30])
    if _PY_TB_HEADER.search(head):
        return True
    if _FLASK_HEADER.search(head):
        return True
    # Stack frame fragments: detect "  File "path", line N, in func" pattern
    frame_lines = sum(
        1 for line in sample_lines[:20]
        if re.match(r'^\s+File "', line) or re.match(r'^\s*File "', line)
    )
    if frame_lines >= 2:
        return True
    return False


def is_go_panic(sample_lines: list[str], full_text: str) -> bool:
    """True when the text block is a Go goroutine dump or panic output."""
    head = "\n".join(sample_lines[:30])
    if not _GO_GOROUTINE.search(head):
        return False
    # Need at least one Go-style stack frame line (tab-indented path with .go)
    go_frame_lines = sum(
        1 for line in sample_lines[:30]
        if re.match(r"^\s+\S+\.go:\d+", line)
    )
    return go_frame_lines >= 1


def is_exception_group(sample_lines: list[str], full_text: str) -> bool:
    """True when the text block contains an ExceptionGroup."""
    head = "\n".join(sample_lines[:30])
    return bool(_EG_HEADER.search(head))
