"""
logZilla3000 — Универсальный парсер логов.
Очищает логи от мусора и преобразует в JSON.

Поддерживаемые форматы:
- CSV (с разделителями: запятая, точка с запятой, табуляция)
- Текстовые логи (Apache, Nginx, syslog, произвольные)
- JSON (валидация и нормализация)
- JSONL / NDJSON (JSON Lines)
- Полуструктурированные логи (смешанные форматы)

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
