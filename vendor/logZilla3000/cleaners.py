"""
Модуль очистки логов от мусора.

Предоставляет набор фильтров и трансформаций для подготовки
сырых логов к структурированному преобразованию.
"""

import re
import html
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class LogCleaner:
    """Очиститель логов от мусора и нормализатор данных."""

    # ANSI escape-последовательности (цвета, курсор и т.д.)
    ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?m")

    # HTML-теги
    HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

    # Дублирующиеся пробелы
    MULTIPLE_SPACES = re.compile(r" {2,}")

    # Дублирующиеся пустые строки
    MULTIPLE_NEWLINES = re.compile(r"\n{3,}")

    # Стандартные паттерны мусора
    GARBAGE_PATTERNS = [
        # Стек-трейсы Java (можно отключить)
        re.compile(r"^\s*at\s+\S+\.\S+\(.*?\)\s*$", re.MULTILINE),
        # Hex-дампы
        re.compile(r"^[0-9a-fA-F]{4,}:\s+[0-9a-fA-F ]{2,}", re.MULTILINE),
        # Строки состоящие только из спецсимволов
        re.compile(r"^[=\-_*#]{3,}\s*$", re.MULTILINE),
        # BOM-маркер
        re.compile(r"^\ufeff"),
        # Нулевые байты
        re.compile(r"\x00"),
        # Управляющие символы (кроме \n, \r, \t)
        re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]"),
    ]

    # Паттерны для извлечения полезных данных
    USEFUL_PATTERNS = {
        "ip_address": re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        "url": re.compile(
            r"https?://[^\s<>\"']+" 
        ),
        "email": re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        ),
        "timestamp_iso": re.compile(
            r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
            r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
        ),
        "timestamp_common": re.compile(
            r"\b\d{2}[/.]\d{2}[/.]\d{2,4}\s+\d{2}:\d{2}:\d{2}\b"
        ),
        "http_method": re.compile(
            r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|CONNECT|TRACE)\b"
        ),
        "http_status": re.compile(
            r"\b(?:HTTP/[\d.]+\s+)?(\d{3})\b"
        ),
        "uuid": re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "json_snippet": re.compile(
            r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        ),
        "xml_snippet": re.compile(
            r"<[a-zA-Z][^>]*>.*?</[a-zA-Z]+>|<[a-zA-Z][^>]*/>"
        ),
    }

    def __init__(
        self,
        remove_ansi: bool = True,
        remove_html: bool = True,
        remove_duplicates: bool = True,
        strip_lines: bool = True,
        normalize_whitespace: bool = True,
        remove_empty_lines: bool = True,
        custom_garbage_patterns: Optional[list] = None,
        keep_patterns: Optional[list] = None,
    ):
        """
        Инициализация очистителя логов.

        Args:
            remove_ansi: Удалять ANSI escape-последовательности
            remove_html: Удалять HTML-теги
            remove_duplicates: Удалять дублирующиеся строки
            strip_lines: Удалять пробелы в начале/конце строк
            normalize_whitespace: Нормализовать пробелы
            remove_empty_lines: Удалять пустые строки
            custom_garbage_patterns: Список дополнительных regex для мусора
            keep_patterns: Список regex для строк, которые НЕ нужно удалять
        """
        self.remove_ansi = remove_ansi
        self.remove_html = remove_html
        self.remove_duplicates = remove_duplicates
        self.strip_lines = strip_lines
        self.normalize_whitespace = normalize_whitespace
        self.remove_empty_lines = remove_empty_lines
        self.custom_garbage_patterns = custom_garbage_patterns or []
        self.keep_patterns = keep_patterns or []

        # Прекомпилированные паттерны (для производительности)
        self._compiled_garbage_patterns: list[re.Pattern] = [
            re.compile(p) if isinstance(p, str) else p
            for p in self.custom_garbage_patterns
        ]
        self._compiled_keep_patterns: list[re.Pattern] = [
            re.compile(p) if isinstance(p, str) else p
            for p in self.keep_patterns
        ]

    def clean(self, raw_data: str, skip_duplicates: bool = False) -> str:
        """
        Полная очистка сырых логов.

        Args:
            raw_data: Сырые лог-данные в виде строки
            skip_duplicates: Пропустить дедупликацию, даже если
                remove_duplicates=True. Используется для CSV/TSV,
                где дубликаты строк — легитимные данные.

        Returns:
            Очищенная строка
        """
        result = raw_data

        # Удаление BOM и нулевых байтов
        result = self.GARBAGE_PATTERNS[3].sub("", result)
        result = self.GARBAGE_PATTERNS[4].sub("", result)
        # Удаление управляющих символов (кроме \n, \r, \t)
        result = self.GARBAGE_PATTERNS[5].sub("", result)

        # Удаление ANSI escape-последовательностей
        if self.remove_ansi:
            result = self.ANSI_PATTERN.sub("", result)

        # Удаление HTML-тегов (перед декодированием сущностей)
        if self.remove_html:
            result = self.HTML_TAG_PATTERN.sub("", result)

        # Декодирование HTML-сущностей
        result = html.unescape(result)

        # Удаление пользовательских паттернов мусора (предкомпилированные)
        for pattern in self._compiled_garbage_patterns:
            result = pattern.sub("", result)

        # Обработка построчно
        lines = result.split("\n")

        if self.strip_lines:
            lines = [line.strip() for line in lines]

        if self.remove_empty_lines:
            lines = [line for line in lines if line]

        # Дедупликация: пропускаем для CSV/TSV (дубликаты — легитимные данные)
        if self.remove_duplicates and not skip_duplicates:
            lines = self._remove_duplicate_lines(lines)

        result = "\n".join(lines)

        # Нормализация пробелов
        if self.normalize_whitespace:
            result = self.MULTIPLE_SPACES.sub(" ", result)
            result = self.MULTIPLE_NEWLINES.sub("\n\n", result)

        return result.strip()

    def clean_json_content(self, content: str) -> str:
        """Минимальная очистка для JSON/JSONL — не ломает структуру.

        Удаляет только BOM-маркер, нулевые байты и управляющие символы,
        сохраняя валидность JSON.

        Args:
            content: Строка с JSON/JSONL данными

        Returns:
            Очищенная строка с сохранённой JSON-структурой
        """
        result = self.GARBAGE_PATTERNS[3].sub("", content)   # BOM
        result = self.GARBAGE_PATTERNS[4].sub("", result)    # Нулевые байты
        result = self.GARBAGE_PATTERNS[5].sub("", result)    # Управляющие символы
        return result.strip()

    def clean_tabular(self, content: str) -> str:
        """Щадящая очистка для CSV/TSV — НЕ искажает содержимое ячеек.

        Полный clean() схлопывает двойные пробелы, делает html.unescape и
        strip строк — для встроенного в колонку JSON/дампа структур/SQL это
        тихое искажение данных. Здесь убираем только BOM, нулевые байты,
        управляющие символы и полностью пустые строки, сохраняя кавычки,
        пробелы и экранирование внутри ячеек.

        Допущение: запись = одна строка (payload не содержит сырых переводов
        строки). Для табличных экспортов Loki/k8s/JSON-in-CSV это выполняется.
        """
        result = self.GARBAGE_PATTERNS[3].sub("", content)   # BOM
        result = self.GARBAGE_PATTERNS[4].sub("", result)    # нулевые байты
        result = self.GARBAGE_PATTERNS[5].sub("", result)    # управляющие символы
        lines = [ln for ln in result.split("\n") if ln.strip()]
        return "\n".join(lines)

    def _remove_duplicate_lines(self, lines: list) -> list:
        """Удаление дублирующихся строк с сохранением порядка.

        Keep-паттерны сохраняют ВСЕ совпадения (даже дубликаты).
        Обычные дубликаты удаляются.
        """
        seen: set[str] = set()
        result: list[str] = []
        for line in lines:
            # Проверяем, не нужно ли сохранить строку принудительно
            keep = any(p.search(line) for p in self._compiled_keep_patterns)

            if keep:
                # keep-паттерн: сохраняем все совпадения, не трогаем seen
                result.append(line)
            elif line not in seen:
                # Обычная строка: добавляем только если не было ранее
                seen.add(line)
                result.append(line)

        return result

    def extract_fields(self, text: str) -> dict:
        """
        Извлечение структурированных полей из текста лога.

        Args:
            text: Очищенный текст лога

        Returns:
            Словарь с извлечёнными полями
        """
        fields = {}

        for name, pattern in self.USEFUL_PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                # Убираем дубликаты, сохраняя порядок
                unique_matches = list(dict.fromkeys(matches))
                if len(unique_matches) == 1:
                    fields[name] = unique_matches[0]
                else:
                    fields[name] = unique_matches

        return fields

    def filter_by_level(
        self, lines: list, levels: Optional[list] = None
    ) -> list:
        """
        Фильтрация строк по уровню логирования.

        Args:
            lines: Список строк лога
            levels: Список уровней для сохранения (например ['ERROR', 'WARN'])
                    Если None — возвращаются все строки

        Returns:
            Отфильтрованный список строк
        """
        if levels is None:
            return lines

        level_pattern = re.compile(
            r"\b(" + "|".join(re.escape(l) for l in levels) + r")\b",
            re.IGNORECASE,
        )

        return [line for line in lines if level_pattern.search(line)]

    def filter_by_date_range(
        self,
        lines: list,
        start: Optional[str] = None,
        end: Optional[str] = None,
        keep_no_timestamp: bool = True,
    ) -> list:
        """
        Фильтрация строк по диапазону дат.

        Args:
            lines: Список строк лога
            start: Начальная дата в формате ISO (опционально)
            end: Конечная дата в формате ISO (опционально)
            keep_no_timestamp: Сохранять строки без распознаваемого таймстампа
                              (по умолчанию True — обратная совместимость)

        Returns:
            Отфильтрованный список строк
        """
        if start is None and end is None:
            return lines

        start_dt = (
            datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        )
        end_dt = (
            datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None
        )

        result: list[str] = []
        for line in lines:
            # Пробуем найти timestamp в строке
            ts_match = self.USEFUL_PATTERNS["timestamp_iso"].search(line)
            if not ts_match:
                ts_match = self.USEFUL_PATTERNS["timestamp_common"].search(line)

            if not ts_match:
                # Строка без таймстампа
                if keep_no_timestamp:
                    result.append(line)
                continue

            try:
                ts_str = ts_match.group()
                # Пробуем разные форматы
                for fmt in (
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                    "%d.%m.%Y %H:%M:%S",
                    "%d/%m/%Y %H:%M:%S",
                ):
                    try:
                        line_dt = datetime.strptime(
                            ts_str.split(".")[0].split("+")[0].split("Z")[0],
                            fmt,
                        )
                        break
                    except ValueError:
                        continue
                else:
                    # Таймстамп найден, но не распознан
                    if keep_no_timestamp:
                        result.append(line)
                    continue

                if start_dt and line_dt < start_dt:
                    continue
                if end_dt and line_dt > end_dt:
                    continue

                result.append(line)

            except (ValueError, IndexError):
                if keep_no_timestamp:
                    result.append(line)

        return result
