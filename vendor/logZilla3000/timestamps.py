"""Канонизация времени записи: любой распознанный таймстамп → (ISO-8601 UTC, epoch).

Зачем: время в логах приходит в десятке несовместимых форматов (ISO, BSD-syslog,
Apache, klog, Unix epoch в s/ms/µs/ns…). Пока оно остаётся сырой строкой исходного
формата — нельзя ни сортировать, ни фильтровать по дате надёжно, ни коррелировать
записи разных форматов. Этот модуль сводит всё к двум каноническим полям записи:
`_ts` (epoch-секунды, float) и `_ts_iso` (ISO-8601 в UTC). Оригинальное поле не
трогаем.

Допущение: таймстамп без зоны трактуется как UTC (детерминизм; локаль машины не
влияет на результат). Год-less форматы (BSD `Jan 15 …`, klog `MM-DD …`) получают
текущий год UTC.

Без сторонних зависимостей — только stdlib datetime/re.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Поля записи, в которых ожидается таймстамп (порядок = приоритет).
TS_FIELDS: tuple[str, ...] = (
    "timestamp", "ts", "time", "@timestamp", "asctime", "datetime",
    "event_time", "eventtime", "time_local", "error_time", "loki_ts", "date",
)

# --- Грамматики таймстампов (якорь ^…$, разбор по именованным группам) --------
_ISO = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[T ]"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:[.,](?P<frac>\d{1,9}))?"
    r"\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)
_SLASH_YMD = re.compile(
    r"^(?P<y>\d{4})/(?P<mo>\d{2})/(?P<d>\d{2})[ T]"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:[.,](?P<frac>\d+))?$"
)
_APACHE = re.compile(
    r"^(?P<d>\d{1,2})/(?P<mon>[A-Za-z]{3})/(?P<y>\d{4}):"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:\s+(?P<tz>[+-]\d{4}))?$"
)
_BSD = re.compile(
    r"^(?P<mon>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})$"
)
# klog/glog после нормализации в text_parser: "MM-DD HH:MM:SS.frac" (год-less).
_MMDD = re.compile(
    r"^(?P<mo>\d{2})-(?P<d>\d{2})\s+"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:[.,](?P<frac>\d+))?$"
)
# Европейский: dd.mm.yyyy ; американский: mm/dd/yyyy. Различаем по разделителю.
_EURO_DOT = re.compile(
    r"^(?P<d>\d{1,2})\.(?P<mo>\d{1,2})\.(?P<y>\d{2,4})[ T]"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:[.,](?P<frac>\d+))?$"
)
_US_SLASH = re.compile(
    r"^(?P<mo>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{2,4})[ T]"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:[.,](?P<frac>\d+))?$"
)
# Чисто числовой токен (epoch как строка).
_NUMERIC = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _parse_tz(tz: Optional[str]) -> Optional[timezone]:
    """'Z'/'+03:00'/'+0300' → timezone; None → None (вызывающий примет UTC)."""
    if not tz or tz == "Z":
        return timezone.utc if tz == "Z" else None
    sign = 1 if tz[0] == "+" else -1
    body = tz[1:].replace(":", "")
    hh, mm = int(body[:2]), int(body[2:4])
    return timezone(sign * timedelta(hours=hh, minutes=mm))


def _emit(dt: datetime) -> tuple[str, float]:
    """datetime (aware/naive) → (ISO-8601 UTC с 'Z', epoch-секунды)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # без зоны → UTC (детерминизм)
    epoch = dt.timestamp()
    iso = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return iso, epoch


def _build(
    y: int, mo: int, d: int, h: int, mi: int, s: int,
    frac: Optional[str] = None, tz: Optional[str] = None,
) -> Optional[tuple[str, float]]:
    micro = int((frac + "000000")[:6]) if frac else 0
    try:
        dt = datetime(y, mo, d, h, mi, s, micro, tzinfo=_parse_tz(tz))
    except ValueError:
        return None
    return _emit(dt)


def _from_epoch_number(x: float) -> Optional[tuple[str, float]]:
    """Целое/дробное → epoch. Масштаб по величине: s/ms/µs/ns.

    Нижняя граница 1e8 (~1973) отсекает мелкие числа (порты/счётчики/коды), которые
    не являются недавним временем — защита от ложной коэрции."""
    ax = abs(x)
    if ax < 1e8:
        return None
    if ax < 1e11:
        sec = float(x)
    elif ax < 1e14:
        sec = x / 1e3
    elif ax < 1e17:
        sec = x / 1e6
    elif ax < 1e20:
        sec = x / 1e9
    else:
        return None
    try:
        dt = datetime.fromtimestamp(sec, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return _emit(dt)


def normalize_ts(value: Any) -> Optional[tuple[str, float]]:
    """Любой распознанный таймстамп → (ISO-8601 UTC, epoch-секунды) или None.

    Принимает строку, int или float (Unix epoch). Только время без даты не
    нормализуем (нет якоря для epoch) — возвращаем None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch_number(value)
    if not isinstance(value, str):
        return None

    s = value.strip()
    if not s:
        return None

    m = _ISO.match(s)
    if m:
        g = m.groupdict()
        return _build(int(g["y"]), int(g["mo"]), int(g["d"]), int(g["h"]),
                      int(g["mi"]), int(g["s"]), g["frac"], g["tz"])

    m = _SLASH_YMD.match(s)
    if m:
        g = m.groupdict()
        return _build(int(g["y"]), int(g["mo"]), int(g["d"]), int(g["h"]),
                      int(g["mi"]), int(g["s"]), g["frac"])

    m = _APACHE.match(s)
    if m:
        g = m.groupdict()
        mon = _MONTHS.get(g["mon"].lower())
        if mon:
            return _build(int(g["y"]), mon, int(g["d"]), int(g["h"]),
                          int(g["mi"]), int(g["s"]), None, g["tz"])

    m = _EURO_DOT.match(s)
    if m:
        g = m.groupdict()
        y = int(g["y"])
        if y < 100:
            y += 2000
        return _build(y, int(g["mo"]), int(g["d"]), int(g["h"]),
                      int(g["mi"]), int(g["s"]), g["frac"])

    m = _US_SLASH.match(s)
    if m:
        g = m.groupdict()
        y = int(g["y"])
        if y < 100:
            y += 2000
        return _build(y, int(g["mo"]), int(g["d"]), int(g["h"]),
                      int(g["mi"]), int(g["s"]), g["frac"])

    now_year = datetime.now(timezone.utc).year
    m = _BSD.match(s)
    if m:
        g = m.groupdict()
        mon = _MONTHS.get(g["mon"].lower())
        if mon:
            return _build(now_year, mon, int(g["d"]), int(g["h"]),
                          int(g["mi"]), int(g["s"]))

    m = _MMDD.match(s)
    if m:
        g = m.groupdict()
        return _build(now_year, int(g["mo"]), int(g["d"]), int(g["h"]),
                      int(g["mi"]), int(g["s"]), g["frac"])

    # Чисто числовая строка — epoch.
    if _NUMERIC.match(s):
        try:
            num = float(s) if ("." in s) else int(s)
        except ValueError:
            return None
        return _from_epoch_number(num)

    return None


def _record_ts(record: dict) -> Optional[tuple[str, float]]:
    """Находит первое таймстамп-несущее поле записи и нормализует его."""
    for key in TS_FIELDS:
        if key in record:
            res = normalize_ts(record[key])
            if res is not None:
                return res
    return None


def add_canonical_ts(data: Any, enabled: bool = True) -> Any:
    """Проставляет `_ts` (epoch) и `_ts_iso` (ISO-8601 UTC) каждой записи списка.

    Работает только со списком записей (list[dict]). Если запись уже несёт `_ts`
    или таймстамп не распознан — не трогаем. Прикладные поля не меняем.
    """
    if not enabled or not isinstance(data, list):
        return data
    for record in data:
        if not isinstance(record, dict) or "_ts" in record:
            continue
        res = _record_ts(record)
        if res is not None:
            iso, epoch = res
            record["_ts"] = epoch
            record["_ts_iso"] = iso
    return data
