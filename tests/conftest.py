"""Bootstrap пути для тестов: делаем импортируемыми пакет `app` и ядро из vendor/.

`app` подключает ядро автоматически (app/__init__.py добавляет vendor/ в sys.path),
но тесты ядра (vendor/logZilla3000/tests) импортируют logZilla3000 напрямую — поэтому
vendor/ кладём в путь и здесь.
"""

import sys
from pathlib import Path

_STUDIO_ROOT = Path(__file__).resolve().parents[1]   # logzilla-studio-next/
for _p in (_STUDIO_ROOT, _STUDIO_ROOT / "vendor"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
