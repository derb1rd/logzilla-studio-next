"""logzilla-studio — HTTP/JSON-сервис и web-UI поверх ядра logZilla3000.

Отдельный проект (живёт ВНЕ стабильного репозитория ядра): граница UI ↔ ядро
вынесена на HTTP/JSON. Ядро (пакет logZilla3000) переиспользуется как есть —
стабильный проект не изменяется и не зависит от studio.

Bootstrap: находим стабильный проект с пакетом logZilla3000 и добавляем его в
sys.path (импорт без установки). Поиск (в порядке приоритета):
  1) переменная окружения LOGZILLA3000_HOME;
  2) соседняя папка ~/Downloads/logzilla3000-project (раскладка по умолчанию);
  3) несколько типовых расположений рядом с этим проектом.
"""

import os
import sys
from pathlib import Path

__version__ = "0.2.0-beta1"


def _find_core_root() -> Path | None:
    """Возвращает каталог, содержащий пакет logZilla3000, или None."""
    here = Path(__file__).resolve()
    candidates: list[Path] = []

    env = os.environ.get("LOGZILLA3000_HOME")
    if env:
        candidates.append(Path(env).expanduser())

    # Вендоренная копия ядра внутри самодостаточного дистрибутива (Вариант А:
    # «отдал запустил поехал» — папка с vendor/ рядом с app/, ничего ставить не нужно).
    candidates.append(here.parents[1] / "vendor")

    # Соседний стабильный проект (раскладка ~/Downloads/{logzilla-studio, logzilla3000-project}).
    candidates.append(here.parents[2] / "logzilla3000-project")
    candidates.append(here.parents[1] / "logzilla3000-project")
    candidates.append(Path.home() / "Downloads" / "logzilla3000-project")

    for c in candidates:
        if (c / "logZilla3000" / "__init__.py").is_file():
            return c
    return None


_CORE_ROOT = _find_core_root()
if _CORE_ROOT is None:
    raise RuntimeError(
        "Не найден пакет ядра logZilla3000.\n"
        "Укажите путь к стабильному проекту через переменную окружения, например:\n"
        "  export LOGZILLA3000_HOME=/путь/к/logzilla3000-project\n"
        "и запустите снова."
    )
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))
