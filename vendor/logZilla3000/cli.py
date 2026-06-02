"""
CLI-интерфейс универсального парсера логов.

Использование:
    python3 -m logZilla3000 файл.csv
    python3 -m logZilla3000 файл.log
    python3 -m logZilla3000 файл.json
    python3 -m logZilla3000 файл1.csv файл2.log файл3.json
"""

import argparse
import json
import logging
import sys
import os
from pathlib import Path
from typing import Any, Optional

from .parser import UniversalLogParser
from .sql_formatter import unescape_sql_in_json

logger = logging.getLogger(__name__)


def get_default_output_dir() -> Path:
    """Возвращает путь к выходной папке по умолчанию.

    Использует ~/Desktop/logzilla3000-output вместо ./output,
    чтобы не зависеть от текущей рабочей директории (CWD).
    """
    desktop = Path.home() / "Desktop"
    output_dir = desktop / "logzilla3000-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_output_path(input_path: str) -> str:
    """
    Генерация имени выходного файла.
    файл.csv → файл_formatted.json
    файл.log → файл_formatted.json
    файл.json → файл_formatted.json
    """
    path = Path(input_path)
    name = path.stem  # имя без расширения
    output_dir = get_default_output_dir()
    return str(output_dir / f"{name}_formatted.json")


def write_result_to_file(
    data: Any,
    output_path: str,
    indent: Optional[int] = 2,
    format_sql: bool = True,
) -> None:
    """
    Запись результата парсинга в JSON-файл.

    Если format_sql=True, заменяет \\n/\\t на реальные символы
    внутри SQL-полей для читаемости.

    Args:
        data: Данные для сериализации
        output_path: Путь к выходному файлу
        indent: Отступы в JSON (None для компактного формата)
        format_sql: Применять unescape для SQL-полей
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    json_str = json.dumps(data, ensure_ascii=False, indent=indent, default=str)
    if format_sql:
        json_str = unescape_sql_in_json(json_str)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json_str)
        f.write("\n")
    logger.info("Результат записан в %s", output_path)


def main():
    parser = argparse.ArgumentParser(
        description="Универсальный парсер логов — очищает мусор и конвертирует в JSON",
    )

    parser.add_argument(
        "inputs",
        nargs="+",
        help="Путь к файлу логов",
    )
    parser.add_argument(
        "-o", "--output",
        help="Путь к выходному JSON-файлу (по умолчанию: <имя>_formatted.json)",
    )
    parser.add_argument(
        "--levels",
        help="Фильтр по уровням логирования (через запятую)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Компактный JSON (без отступов)",
    )
    parser.add_argument(
        "--no-expand-message",
        action="store_true",
        help="Не раскрывать вложенные JSON/Python-dict в поле message",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Увеличить подробность вывода (-v: INFO, -vv: DEBUG)",
    )

    args = parser.parse_args()

    # Настройка логирования
    log_level = logging.WARNING
    if args.verbose >= 2:
        log_level = logging.DEBUG
    elif args.verbose >= 1:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    # Подготовка параметров
    kwargs: dict[str, Any] = {
        "indent": None if args.compact else 2,
    }

    if args.levels:
        kwargs["log_levels"] = [l.strip() for l in args.levels.split(",")]

    if args.no_expand_message:
        kwargs["expand_message"] = False

    log_parser = UniversalLogParser(**kwargs)

    # Обработка файлов
    for input_path in args.inputs:
        output_path = args.output or get_output_path(input_path)
        try:
            result = log_parser.parse_file(input_path)
            write_result_to_file(result, output_path, indent=kwargs["indent"], format_sql=log_parser.format_sql)
            logger.info("✅ %s → %s", input_path, output_path)
            print(f"✅ {input_path} → {output_path}", file=sys.stderr)
        except Exception as e:
            logger.error("❌ %s: %s", input_path, e)
            print(f"❌ {input_path}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
