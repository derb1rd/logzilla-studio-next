"""Единый источник истины по уровню логирования записи.

Раньше логика «определить уровень записи» жила в ДВУХ местах с разной полнотой:
ядро канонизировало синонимы (text_parser._LEVEL_CANON: NOTICE/SEVERE/PANIC/…),
а сервисный слой (app/parse_service._level_token) знал лишь короткий набор
DEBUG|INFO|WARN|ERROR|FATAL|CRITICAL|TRACE — из-за чего метрики и фильтрация
расходились (NOTICE-запись считалась ядром, но не сервисом).

Этот модуль — общий: и постпроходный фильтр ядра (parser._filter_records), и
метрики сервиса берут уровень через record_level(). Один разбор → один результат.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .text_parser import _LEVEL_ALT, canon_level

# Явное поле уровня записи (структурированные логи кладут его в один из этих ключей).
LEVEL_KEYS: tuple[str, ...] = (
    "level", "levelname", "log_level", "loglevel", "severity", "lvl",
)
# Текстовые поля строки лога — fallback для логов без явного поля уровня
# (plain-text → message/raw). Уровень берём ИЗ НАЧАЛА строки (первый токен),
# а не из произвольного места: иначе слово ERROR в теле сообщения завышало бы счёт.
TEXT_KEYS: tuple[str, ...] = ("message", "msg", "raw", "line", "text", "log")

# Канонические группы для агрегации (метрики/фильтр). canon_level уже свёл
# синонимы (WARNING→WARN, ERR→ERROR, SEVERE→ERROR, PANIC→FATAL, …).
ERROR_LEVELS: frozenset[str] = frozenset({"ERROR", "FATAL", "CRITICAL", "ALERT", "EMERG"})
WARN_LEVELS: frozenset[str] = frozenset({"WARN"})

# Уровень-токен где угодно в строковом значении поля уровня (level="INFO: ...").
_LEVEL_IN_VALUE = re.compile(r"\b(?P<lvl>" + _LEVEL_ALT + r")\b", re.IGNORECASE)
# Уровень-токен В НАЧАЛЕ текстовой строки (для fallback по message/raw).
_LEVEL_AT_HEAD = re.compile(
    r"^[\[(\s]*(?P<lvl>" + _LEVEL_ALT + r")\b", re.IGNORECASE
)

# Числовой уровень → канон. RFC5424/GELF severity (0–7) и pino/bunyan level
# (10–60). Диапазоны разводят две шкалы; canon_level не трогаем — значения уже
# канонические.
_SYSLOG_SEVERITY: tuple[str, ...] = (
    "EMERG", "ALERT", "CRITICAL", "ERROR", "WARN", "INFO", "INFO", "DEBUG",
)
_PINO_LEVELS: dict[int, str] = {
    10: "TRACE", 20: "DEBUG", 30: "INFO", 40: "WARN", 50: "ERROR", 60: "FATAL",
}


def level_from_number(n: Any) -> Optional[str]:
    """Числовой уровень (syslog severity 0–7 или pino 10–60) → каноническое имя."""
    if not isinstance(n, int) or isinstance(n, bool):
        return None
    if 0 <= n <= 7:
        return _SYSLOG_SEVERITY[n]
    if 10 <= n <= 60:
        return _PINO_LEVELS.get(min(60, round(n / 10) * 10))
    return None


def record_level(record: dict) -> Optional[str]:
    """Канонический уровень записи: сначала явное поле уровня, затем «голова» текста.

    Возвращает один из канонических токенов (см. text_parser._LEVEL_CANON) либо None.
    """
    if not isinstance(record, dict):
        return None

    for key in LEVEL_KEYS:
        v = record.get(key)
        if isinstance(v, str):
            m = _LEVEL_IN_VALUE.search(v)
            if m:
                return canon_level(m.group("lvl"))
        elif isinstance(v, int) and not isinstance(v, bool):
            lvl = level_from_number(v)
            if lvl:
                return lvl

    # Fallback: уровень в начале текста строки лога.
    for key in TEXT_KEYS:
        v = record.get(key)
        if isinstance(v, str):
            m = _LEVEL_AT_HEAD.match(v)
            if m:
                return canon_level(m.group("lvl"))
    return None


def matches_levels(record: dict, wanted: list[str]) -> bool:
    """True, если уровень записи входит в wanted (с канонизацией обеих сторон).

    Пустой/None wanted → пропускаем всё (фильтр выключен). Записи без уровня
    при заданном фильтре отбрасываются — это и есть смысл фильтра «только ERROR».
    """
    if not wanted:
        return True
    lvl = record_level(record)
    if lvl is None:
        return False
    canon_wanted = {canon_level(w) for w in wanted}
    return lvl in canon_wanted


def count_levels(records: list[dict]) -> tuple[int, int]:
    """(errors, warnings) по каноническим группам — для метрик."""
    errors = warnings = 0
    for rec in records:
        lvl = record_level(rec)
        if lvl in ERROR_LEVELS:
            errors += 1
        elif lvl in WARN_LEVELS:
            warnings += 1
    return errors, warnings
