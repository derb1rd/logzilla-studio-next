"""
Структурный разбор «произвольных» текстовых логов без пользовательского паттерна.

Самый частый формат логов в проде — это НЕ Apache/Nginx/CSV, а строка вида

    2025-01-15 10:30:00,123 INFO  [main] com.foo.Bar - Application started
    2025-01-15T10:30:00.123Z ERROR something failed
    I0102 03:04:05.123456  1234 server.go:42] Starting server   (klog/glog)

Раньше такие строки уезжали в «извлечение полей регэкспами» (extract_fields),
где \\b\\d{3}\\b ловил миллисекунды как http_status, а {...} — как json_snippet.
Результат — мусорные поля и потеря структуры (timestamp/level/message).

Этот модуль разбирает строку по «голове»: timestamp → level → thread → logger,
остаток — message. Если ни timestamp, ни level не нашлись, строка считается
неструктурированной и возвращается как None (вызывающий обернёт в {"message": ...}).
Никакого «угадывания» лишних полей — только то, что реально стоит в начале строки.
"""

import re
from typing import Any, Optional

# --- Уровни логирования: распознавание и канонизация ----------------------
# Канонизируем синонимы к одному виду, чтобы фильтрация по уровню и группировка
# были предсказуемыми (WARNING/WARN → WARN, ERR → ERROR и т.д.).
_LEVEL_CANON: dict[str, str] = {
    "TRACE": "TRACE", "FINEST": "TRACE", "FINER": "TRACE",
    "DEBUG": "DEBUG", "FINE": "DEBUG", "DBG": "DEBUG", "VERBOSE": "DEBUG",
    "INFO": "INFO", "INFORMATION": "INFO", "NOTICE": "INFO",
    "WARN": "WARN", "WARNING": "WARN",
    "ERROR": "ERROR", "ERR": "ERROR", "SEVERE": "ERROR",
    "CRIT": "CRITICAL", "CRITICAL": "CRITICAL",
    "ALERT": "ALERT", "EMERG": "EMERG", "EMERGENCY": "EMERG",
    "FATAL": "FATAL", "PANIC": "FATAL",
}
# Сортируем по убыванию длины, чтобы WARNING матчился раньше WARN и т.п.
_LEVEL_ALT = "|".join(
    sorted((re.escape(k) for k in _LEVEL_CANON), key=len, reverse=True)
)

# Таймстамп в начале строки. Допускаем необязательную обёртку в [...].
# Порядок важен: ISO (с T или пробелом) — первым, как самый частый и однозначный.
_TS_PATTERNS: tuple[re.Pattern, ...] = (
    # ISO-8601: 2025-01-15T10:30:00.123Z / 2025-01-15 10:30:00,123 +03:00
    re.compile(
        r"\[?(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
        r"(?:[.,]\d{1,9})?(?:\s?Z|\s?[+-]\d{2}:?\d{2})?)\]?"
    ),
    # YYYY/MM/DD HH:MM:SS — Go log по умолчанию, nginx error.log, и т.п.
    re.compile(
        r"\[?(?P<ts>\d{4}/\d{2}/\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?)\]?"
    ),
    # Apache/Nginx-стиль внутри строки логов приложений: 10/Oct/2000:13:55:36 -0700
    re.compile(
        r"\[?(?P<ts>\d{1,2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}"
        r"(?:\s[+-]\d{4})?)\]?"
    ),
    # BSD-syslog: Jan 15 10:30:00
    re.compile(r"\[?(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\]?"),
    # Европейский/американский: 15.01.2025 10:30:00 / 01/15/2025 10:30:00
    re.compile(
        r"\[?(?P<ts>\d{2}[/.]\d{2}[/.]\d{2,4}[ T]\d{2}:\d{2}:\d{2}"
        r"(?:[.,]\d{1,9})?)\]?"
    ),
    # Только время (без даты): 10:30:00.123 — частая «голова» в дев-логах
    re.compile(r"\[?(?P<ts>\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?)\]?"),
)

# Уровень: возможно в [..] или (..), возможно с ведущим/замыкающим разделителем.
_LEVEL_RE = re.compile(
    r"^[\[(]?\s*(?P<level>" + _LEVEL_ALT + r")\s*[\])]?(?=$|[\s:;,\]\-|])",
    re.IGNORECASE,
)

# Поток/контекст в квадратных скобках сразу после уровня: [main], [pool-1-thread-2]
_THREAD_RE = re.compile(r"^\[(?P<thread>[^\]]{1,80})\]\s*")

# Логгер с разделителем: "com.foo.Bar - " или "com.foo.Bar: ".
# Требуем точку в имени, иначе бы съедали первое слово обычного сообщения.
_LOGGER_RE = re.compile(r"^(?P<logger>[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)+)\s*[-:]\s+")

# Формат Python logging по умолчанию: "<ts> - <logger> - <LEVEL> - <message>"
# (т.е. имя логгера стоит ПЕРЕД уровнем). Срабатывает только когда уровень не
# нашёлся в голове. Ведущий и замыкающий разделители поглощаем.
_LOGGER_THEN_LEVEL_RE = re.compile(
    r"^[-\s]*(?P<logger>[A-Za-z_][\w.$]*)\s*[-:]\s*"
    r"(?P<level>" + _LEVEL_ALT + r")\b[-:\s]*",
    re.IGNORECASE,
)

# klog/glog (компоненты Kubernetes): Lmmdd hh:mm:ss.uuuuuu threadid file:line] msg
_KLOG_RE = re.compile(
    r"^(?P<lvl>[IWEF])(?P<md>\d{4})\s+(?P<time>\d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"(?P<tid>\d+)\s+(?P<loc>[^\]]+):(?P<line>\d+)\]\s?(?P<message>.*)$"
)
_KLOG_LEVEL = {"I": "INFO", "W": "WARN", "E": "ERROR", "F": "FATAL"}


def canon_level(token: str) -> str:
    """Приводит распознанный уровень к каноническому виду."""
    return _LEVEL_CANON.get(token.upper(), token.upper())


def _parse_klog(line: str) -> Optional[dict[str, Any]]:
    m = _KLOG_RE.match(line)
    if not m:
        return None
    md = m.group("md")
    return {
        "timestamp": f"{md[:2]}-{md[2:]} {m.group('time')}",
        "level": _KLOG_LEVEL[m.group("lvl")],
        "tid": int(m.group("tid")),
        "source": f"{m.group('loc')}:{m.group('line')}",
        "message": m.group("message"),
    }


def parse_generic_line(line: str) -> Optional[dict[str, Any]]:
    """Разбирает одну строку текстового лога в структурированную запись.

    Возвращает dict с частью полей {timestamp, level, thread, logger, message}
    либо None, если строка не выглядит как структурированный лог (нет ни
    таймстампа, ни уровня в голове).
    """
    line = line.strip()
    if not line:
        return None

    # klog имеет собственную жёсткую грамматику — проверяем первой.
    klog = _parse_klog(line)
    if klog is not None:
        return klog

    rec: dict[str, Any] = {}
    rest = line

    # 1. Таймстамп в начале строки.
    for pat in _TS_PATTERNS:
        m = pat.match(rest)
        if m:
            rec["timestamp"] = m.group("ts").strip()
            rest = rest[m.end():].lstrip(" \t")
            break

    # 2. Уровень логирования.
    m = _LEVEL_RE.match(rest)
    if m:
        full = m.group(0)
        token = m.group("level")
        # Голый уровень БЕЗ таймстампа принимаем только если он в скобках,
        # ALL-CAPS, или сразу за ним двоеточие. Иначе проза вроде «Information
        # about the system» ложно получила бы level=INFO.
        bracketed = "[" in full or "(" in full
        followed_by_colon = rest[m.end():m.end() + 1] == ":"
        if "timestamp" in rec or bracketed or token.isupper() or followed_by_colon:
            rec["level"] = canon_level(token)
            rest = rest[m.end():].lstrip(" \t:|-")

    # 2b. Формат Python logging: "<ts> - <logger> - <LEVEL> - <msg>" — уровень не
    #     в голове, а после имени логгера. Пробуем только если уже есть таймстамп
    #     (иначе можно ошибочно срезать начало обычного сообщения).
    if "level" not in rec and "timestamp" in rec:
        m = _LOGGER_THEN_LEVEL_RE.match(rest)
        if m:
            rec["logger"] = m.group("logger")
            rec["level"] = canon_level(m.group("level"))
            rest = rest[m.end():].lstrip()

    # Если в голове не нашлось ни таймстампа, ни уровня — это не структурированный
    # лог. Пусть вызывающий положит всю строку в message.
    if "timestamp" not in rec and "level" not in rec:
        return None

    # 3. Поток/контекст в [..] (опционально).
    m = _THREAD_RE.match(rest)
    if m:
        rec["thread"] = m.group("thread").strip()
        rest = rest[m.end():].lstrip()

    # 4. Имя логгера с разделителем (опционально, только дотированное).
    m = _LOGGER_RE.match(rest)
    if m:
        rec["logger"] = m.group("logger")
        rest = rest[m.end():].lstrip()

    rec["message"] = rest
    return rec
