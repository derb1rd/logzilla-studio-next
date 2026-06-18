"""Сшивка многострочных записей (стек-трейсы, продолжения) в одну запись.

Проблема: чистильщик режет вход по `\n`, поэтому Java/Python стек-трейс распадается
на отдельные записи `{"message": "at com.foo…"}` — теряется связь исключения с его
лог-строкой, а поток засоряется мусорными записями.

Решение — ПОСТконвертационное, на уровне записей (а не текста): и текстовый, и
syslog-конвертеры кладут нераспознанную строку как `{"message": line}`. Когда такая
«голая» запись идёт за структурной (с таймстампом/уровнем) И похожа на начало трейса
(`at …`, `Caused by:`, `…Exception`, `Traceback`, `File "…", line N`, `… N more`),
мы открываем «режим трейса» у родителя и приклеиваем к его полю `stack` ВСЕ
последующие голые строки до следующей структурной записи (тело исключения вроде
«x is null» хинтам не отвечает, но принадлежит трейсу).

Безопасность для free-form текста: если структурной записи-родителя нет (сплошь
голые `{message}` без таймстампов/уровней — обычная проза), трейс не открывается и
ничего не склеивается. Один абзац НЕ схлопывается в одну запись.
"""

from __future__ import annotations

import re
from typing import Any

# Признак начала/продолжения стек-трейса (язык-агностично: JVM, Python, .NET, Go).
_TRACE_START = re.compile(
    r"^(?:"
    r"at\s+\S"                                   # JVM: at com.foo.Bar(...)
    r"|Caused by:"                               # JVM: вложенная причина
    r"|Suppressed:"                              # JVM: подавлённое
    r"|\.{3}\s*\d+\s+more\b"                      # JVM: ... 12 more
    r"|Traceback \(most recent call last\):"     # Python
    r"|File \".*\", line \d+"                     # Python frame
    r"|[\w.$]+(?:Exception|Error|Throwable)\b"    # имя класса исключения
    r"|[A-Za-z_][\w.]*Error: "                    # Python: ValueError: ...
    r"|goroutine \d+ \["                          # Go: goroutine 1 [running]:
    r")"
)


def _is_bare_message(rec: Any) -> bool:
    """Запись — это только `{"message": "<строка>"}` без иных полей.

    Именно так текстовый и syslog конвертеры представляют нераспознанную строку
    (в т.ч. кадр стек-трейса). Такая запись — кандидат в продолжение предыдущей.
    """
    return (
        isinstance(rec, dict)
        and set(rec.keys()) == {"message"}
        and isinstance(rec.get("message"), str)
    )


def _is_structured(rec: Any) -> bool:
    """Запись несёт «голову» (таймстамп/уровень/…), а не просто текст строки."""
    return isinstance(rec, dict) and bool(rec) and not _is_bare_message(rec)


def stitch_records(records: Any) -> Any:
    """Приклеивает строки трейса к `stack` предыдущей структурной записи.

    Возвращает новый список (исходные структурные dict'ы мутируются добавлением
    `stack`). Для не-списка и списков короче 2 — no-op.
    """
    if not isinstance(records, list) or len(records) < 2:
        return records

    out: list[Any] = []
    parent: dict | None = None       # последняя структурная запись
    in_trace = False                 # открыт ли режим сбора трейса у parent
    for rec in records:
        if _is_bare_message(rec) and parent is not None:
            msg = rec["message"]
            if in_trace or _TRACE_START.match(msg):
                in_trace = True
                prev = parent.get("stack")
                parent["stack"] = f"{prev}\n{msg}" if prev else msg
                continue
        out.append(rec)
        # Структурная запись — новый родитель; голая (без родителя) самостоятельна.
        if _is_structured(rec):
            parent = rec
        else:
            parent = None
        in_trace = False
    return out
