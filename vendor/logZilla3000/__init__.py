"""
logZilla3000 — Универсальный парсер логов.
Очищает логи от мусора и преобразует в JSON.

Поддерживаемые форматы:
- CSV/TSV (с разделителями: запятая, точка с запятой, табуляция)
- Текстовые логи (Apache, Nginx, syslog RFC3164/RFC5424, klog/glog, произвольные)
- JSON (валидация и нормализация)
- JSONL / NDJSON (JSON Lines)
- logfmt (key=value)
- CRI / containerd (kubectl logs)
- CEF (ArcSight) / LEEF (IBM QRadar) — security/SIEM
- Полуструктурированные логи (смешанные форматы)

Дополнительно ко всем форматам: канонический timestamp (_ts/_ts_iso из ISO/BSD/
Apache/klog/epoch), сшивка стек-трейсов в поле stack, фильтрация по уровню/дате на
уровне записей. Кастомный текстовый log_format (nginx и т.п.) — через параметр
pattern.

Использование CLI:
    python3 -m logZilla3000 файл.csv
    python3 logparse.py файл.log

Использование GUI:
    python3 -m logZilla3000.gui

Использование как модуля:
    from logZilla3000 import UniversalLogParser
    parser = UniversalLogParser()
    result = parser.parse_file("access.log")
"""

from .parser import UniversalLogParser
from .cleaners import LogCleaner
from .detectors import FormatDetector
from .converters import JSONConverter
from .config import load_config, save_config, get_output_dir

__version__ = "2.1.0"
__all__ = [
    "UniversalLogParser",
    "LogCleaner",
    "FormatDetector",
    "JSONConverter",
    "load_config",
    "save_config",
    "get_output_dir",
]
