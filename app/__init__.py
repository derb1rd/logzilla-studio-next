"""logzilla3000 — HTTP/JSON-сервис и web-UI поверх ядра logZilla3000.

Самодостаточный продукт: ядро (пакет logZilla3000) и его опциональная зависимость
sqlparse вендорятся внутрь — в каталог vendor/ рядом с app/. Граница UI ↔ ядро
вынесена на HTTP/JSON. dev-раскладка совпадает с дистрибутивом («отдал запустил
поехал»): на машине получателя нужен только python3 3.10+.

Bootstrap: добавляем vendor/ в sys.path (импорт без установки). Порядок поиска:
  1) переменная окружения LOGZILLA3000_HOME — dev-override на внешнее ядро;
  2) vendor/ — канон (вендоренная копия).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__version__ = "0.9.18"


def _find_core_root() -> Path | None:
    """Возвращает каталог, содержащий пакет logZilla3000, или None."""
    here = Path(__file__).resolve()
    candidates: list[Path] = []

    # Dev-override: внешнее ядро (для разработки против незавендоренной копии).
    env = os.environ.get("LOGZILLA3000_HOME")
    if env:
        candidates.append(Path(env).expanduser())

    # Канон: вендоренная копия ядра рядом с app/.
    candidates.append(here.parents[1] / "vendor")

    for c in candidates:
        if (c / "logZilla3000" / "__init__.py").is_file():
            return c
    return None


_CORE_ROOT = _find_core_root()
if _CORE_ROOT is None:
    raise RuntimeError(
        "Не найден пакет ядра logZilla3000 в vendor/.\n"
        "Папка повреждена или собрана неполностью. Для разработки против внешнего\n"
        "ядра укажите путь: export LOGZILLA3000_HOME=/путь/к/logzilla3000-project"
    )
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))
