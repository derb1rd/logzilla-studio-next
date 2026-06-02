"""
Модуль конфигурации logZilla3000.

Централизует все настраиваемые параметры приложения.
Поддерживает загрузку из конфигурационного файла и сохранение настроек
между запусками.

Конфигурационный файл: ~/.logzilla3000/config.json
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

# Директория для конфигурации
CONFIG_DIR = Path.home() / ".logzilla3000"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Значения по умолчанию
DEFAULTS: dict[str, Any] = {
    # Кодировка по умолчанию
    "encoding": "utf-8",
    # Отступы в JSON (None = компактный)
    "indent": 2,
    # Очистка
    "remove_ansi": True,
    "remove_html": True,
    "remove_duplicates": True,
    "remove_empty_lines": True,
    # Раскрытие вложенных JSON в message
    "expand_message": True,
    # SQL-форматирование
    "format_sql": True,
    # Выходная папка (пустая строка = ~/Desktop/logzilla3000-output)
    "output_dir": "",
    # Уровни логирования (пустой список = все уровни)
    "log_levels": [],
}


def _ensure_config_dir() -> None:
    """Создаёт директорию для конфигурации, если не существует."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """Загружает конфигурацию из файла.

    Если файл не существует — возвращает значения по умолчанию.
    Если файл повреждён — возвращает значения по умолчанию с предупреждением.

    Returns:
        Словарь с настройками
    """
    config = dict(DEFAULTS)

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Мержим: сохранённые значения перекрывают дефолты
            for key, value in saved.items():
                if key in DEFAULTS:
                    config[key] = value
        except (json.JSONDecodeError, OSError, ValueError):
            # Повреждённый конфиг — используем дефолты
            pass

    return config


def save_config(config: dict[str, Any]) -> None:
    """Сохраняет конфигурацию в файл.

    Сохраняются только параметры из DEFAULTS, лишние ключи игнорируются.

    Args:
        config: Словарь с настройками
    """
    _ensure_config_dir()

    # Фильтруем: сохраняем только известные ключи
    filtered = {
        key: config[key]
        for key in DEFAULTS
        if key in config
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_output_dir(config: Optional[dict[str, Any]] = None) -> str:
    """Возвращает путь к выходной папке из конфигурации.

    Args:
        config: Конфигурация (None = загрузить из файла)

    Returns:
        Абсолютный путь к выходной папке
    """
    if config is None:
        config = load_config()

    output_dir = config.get("output_dir", "")

    if output_dir and os.path.isabs(output_dir):
        return output_dir

    # По умолчанию — ~/Desktop/logzilla3000-output
    desktop = Path.home() / "Desktop"
    return str(desktop / "logzilla3000-output")
