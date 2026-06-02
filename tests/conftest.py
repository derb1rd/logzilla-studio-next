"""Bootstrap пути для тестов: делаем импортируемым пакет `app`.

Ядро logZilla3000 подключается автоматически внутри app/__init__.py
(поиск стабильного проекта + LOGZILLA3000_HOME).
"""

import sys
from pathlib import Path

_STUDIO_ROOT = Path(__file__).resolve().parents[1]   # logzilla-studio/
if str(_STUDIO_ROOT) not in sys.path:
    sys.path.insert(0, str(_STUDIO_ROOT))
