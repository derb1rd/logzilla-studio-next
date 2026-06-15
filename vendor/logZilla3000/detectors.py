"""
Модуль автоопределения формата логов.

Определяет тип входных данных и выбирает подходящий парсер.
"""

import re
import json
import csv
import io
from enum import Enum
from typing import Optional


class LogFormat(Enum):
    """Поддерживаемые форматы логов."""
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    JSONL = "jsonl"  # JSON Lines (один JSON объект на строку)
    APACHE = "apache"
    NGINX = "nginx"
    LOKI_NGINX = "loki_nginx"
    SYSLOG = "syslog"
    TEXT = "text"  # Произвольный текстовый лог
    UNKNOWN = "unknown"


class FormatDetector:
    """Автоматическое определение формата логов."""

    # Паттерны для определения формата
    PATTERNS = {
        LogFormat.APACHE: re.compile(
            r'^(\S+)\s+(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+"(\S+)\s+(\S+)\s+\S+"\s+(\d{3})\s+(\d+|-)'
        ),
        LogFormat.NGINX: re.compile(
            r'^(\S+)\s+-\s+(\S+)\s+\[([^\]]+)\]\s+"(\S+)\s+(\S+)\s+\S+"\s+(\d{3})\s+(\d+)\s+"([^"]*)"\s+"([^"]*)"$'
        ),
        LogFormat.SYSLOG: re.compile(
            r'^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(\S+?)(?:\[\d+\])?:\s+(.*)'
        ),
    }

    # Паттерны для определения Loki-prefixed nginx логов
    LOKI_TS_PREFIX = re.compile(
        r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\t'
    )
    LOKI_NGINX_ACCESS = re.compile(
        r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\t'
        r'\S+\s+-\s+\S+\s+\[[^\]]+\]\s+"\S+\s+\S+\s+\S+"\s+\d{3}'
    )
    LOKI_NGINX_ERROR = re.compile(
        r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\t'
        r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+\[\w+\]'
    )

    # Паттерны для определения уровня логирования
    LOG_LEVEL_PATTERN = re.compile(
        r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|TRACE)\b",
        re.IGNORECASE,
    )

    # Паттерн для определения JSON
    JSON_PATTERN = re.compile(r"^\s*[\{\[]")

    # Паттерн для определения CSV-заголовка.
    # Имена колонок допускают точку и дефис: продуктовые экспорты сплошь и рядом
    # используют их (kubernetes.container_name, event.original, x-request-id).
    # Ведущий «@» — поля Elastic/ECS (@timestamp, @version): Kibana-экспорты сплошь
    # начинаются с такой колонки, без неё весь файл уезжал в текстовый путь.
    # Без этого такой заголовок не матчился → файл уезжал в текстовый путь и
    # парсился reg-экспами вместо разбивки по колонкам.
    CSV_HEADER_PATTERN = re.compile(
        r"^@?[a-zA-Z_][\w.\-]*"
        r"(?:[,;\t]@?[a-zA-Z_][\w.\-]*)+$"
    )

    # Имя одной колонки (проверяется после снятия кавычек csv.reader'ом).
    COLUMN_NAME_PATTERN = re.compile(r"^@?[a-zA-Z_][\w.\-]*$")

    def __init__(self, sample_size: int = 50):
        """
        Инициализация детектора формата.

        Args:
            sample_size: Количество строк для анализа при определении формата
        """
        self.sample_size = sample_size

    def detect(self, data: str) -> LogFormat:
        """
        Определение формата логов.

        Args:
            data: Входные данные в виде строки

        Returns:
            Определённый формат логов
        """
        stripped = data.strip()
        if not stripped:
            return LogFormat.UNKNOWN

        # 0. Многострочный JSON (pretty-printed массив/объект): пробуем разобрать
        # ВЕСЬ текст. Иначе sample из первых N строк обрезает JSON до невалидного,
        # и файл уезжает в текстовый путь (построчный мусор). Дёшево — проверяем
        # только когда текст начинается со скобки.
        if stripped[0] in "{[":
            try:
                json.loads(stripped)
                return LogFormat.JSON
            except (json.JSONDecodeError, ValueError):
                pass

        lines = stripped.split("\n")

        # Берём sample_size строк для анализа
        sample = lines[: self.sample_size]

        # 1. Проверяем JSON / JSONL
        json_format = self._detect_json(sample)
        if json_format:
            return json_format

        # 2. Проверяем CSV / TSV. Сначала csv.reader на сыром тексте (устойчив к
        #    многострочным полям), затем — выборочная проверка как фолбэк.
        csv_format = self._detect_csv_raw(stripped) or self._detect_csv(sample)
        if csv_format:
            return csv_format

        # 2.5. Проверяем Loki-prefixed nginx логи
        loki_nginx = self._detect_loki_nginx(sample)
        if loki_nginx:
            return loki_nginx

        # 3. Проверяем известные форматы (Nginx перед Apache, т.к. Apache паттерн тоже матчит Nginx)
        check_order = [
            LogFormat.NGINX, LogFormat.APACHE, LogFormat.SYSLOG,
        ]
        for fmt in check_order:
            pattern = self.PATTERNS[fmt]
            matches = sum(1 for line in sample if pattern.match(line.strip()))
            if matches >= len(sample) * 0.6:  # 60% совпадений достаточно
                return fmt

        # 4. Проверяем наличие уровня логирования (признак структурированного лога)
        level_matches = sum(
            1 for line in sample if self.LOG_LEVEL_PATTERN.search(line)
        )
        if level_matches >= len(sample) * 0.5:
            return LogFormat.TEXT

        return LogFormat.TEXT

    def detect_delimiter(self, data: str) -> str:
        """
        Определение разделителя для CSV-подобных данных.

        Args:
            data: Входные данные

        Returns:
            Символ разделителя (',', ';', '\\t', '|')
        """
        lines = data.strip().split("\n")
        if len(lines) < 2:
            return ","

        candidates = [",", ";", "\t", "|"]
        best_delimiter = ","
        best_score = -1

        for delimiter in candidates:
            counts = [line.count(delimiter) for line in lines[:10]]
            if not counts:
                continue

            # Все строки должны иметь одинаковое количество разделителей
            if len(set(counts)) == 1 and counts[0] > 0:
                score = counts[0] * 10  # Бонус за консистентность
                if score > best_score:
                    best_score = score
                    best_delimiter = delimiter

        return best_delimiter

    def _detect_json(self, sample: list) -> Optional[LogFormat]:
        """Проверка, является ли данные JSON или JSONL."""
        non_empty = [line.strip() for line in sample if line.strip()]
        if not non_empty:
            return None

        # Проверяем JSONL (каждая строка — отдельный JSON)
        jsonl_count = 0
        for line in non_empty:
            try:
                json.loads(line)
                jsonl_count += 1
            except (json.JSONDecodeError, ValueError):
                pass

        if jsonl_count >= len(non_empty) * 0.5:
            return LogFormat.JSONL

        # Проверяем обычный JSON (весь текст — один JSON)
        full_text = "\n".join(sample)
        try:
            json.loads(full_text)
            return LogFormat.JSON
        except (json.JSONDecodeError, ValueError):
            pass

        return None

    def _detect_csv(self, sample: list) -> Optional[LogFormat]:
        """Проверка, является ли данные CSV/TSV.

        Заголовок валидируем ПОСЛЕ разбора csv.reader'ом (он снимает кавычки),
        а не регэкспом по сырой строке. Иначе экспорты с закавыченными именами
        колонок («"Time","Line",...») не распознавались как CSV и уезжали в
        текстовый путь.
        """
        non_empty = [line.strip() for line in sample if line.strip()]
        if len(non_empty) < 2:
            return None

        # Пробуем разные разделители
        for delimiter, fmt in [(",", LogFormat.CSV), ("\t", LogFormat.TSV), (";", LogFormat.CSV)]:
            try:
                reader = csv.reader(io.StringIO("\n".join(non_empty[:5])), delimiter=delimiter)
                rows = list(reader)
                if len(rows) < 2:
                    continue
                header = rows[0]
                header_count = len(header)
                if header_count < 2:
                    continue
                # Заголовок должен состоять из имён-колонок (после снятия кавычек)
                if not all(self.COLUMN_NAME_PATTERN.match(h.strip()) for h in header):
                    continue
                # Консистентность количества полей в data-строках
                consistent = all(
                    len(row) == header_count or len(row) == header_count - 1
                    for row in rows[1:]
                )
                if consistent:
                    return fmt
            except csv.Error:
                continue

        return None

    def _detect_csv_raw(self, data: str) -> Optional[LogFormat]:
        """CSV-детекция поверх csv.reader на СЫРОМ тексте — устойчива к полям с
        переводами строк (трассировки/JSON в колонке message).

        Наивный split('\\n') в detect() рвёт закавыченные многострочные поля, и
        валидный CSV-экспорт (напр. Kibana с трейсбэком в первых строках) уезжал в
        текстовый путь — а там reg-эвристики выдают мусор (json_snippet/http_status).
        csv.reader корректно склеивает многострочные поля, поэтому проверяем им.
        Читаем лениво только первые logical-rows."""
        for delimiter, fmt in [(",", LogFormat.CSV), ("\t", LogFormat.TSV), (";", LogFormat.CSV)]:
            try:
                reader = csv.reader(io.StringIO(data), delimiter=delimiter)
                rows = []
                for row in reader:
                    rows.append(row)
                    if len(rows) >= 12:
                        break
            except csv.Error:
                continue
            if len(rows) < 2:
                continue
            header = rows[0]
            header_count = len(header)
            if header_count < 2:
                continue
            if not all(self.COLUMN_NAME_PATTERN.match(h.strip()) for h in header):
                continue
            data_rows = [r for r in rows[1:] if r and any(c.strip() for c in r)]
            if not data_rows:
                continue
            # Консистентность числа полей (последняя колонка может быть пустой/опущенной)
            consistent = all(
                len(r) == header_count or len(r) == header_count - 1
                for r in data_rows
            )
            if consistent:
                return fmt
        return None

    def _detect_loki_nginx(self, sample: list) -> Optional[LogFormat]:
        """Проверка, является ли данные Loki-prefixed nginx логами."""
        non_empty = [line.strip() for line in sample if line.strip()]
        if not non_empty:
            return None

        # Считаем строки с Loki timestamp prefix
        loki_prefix_count = sum(
            1 for line in non_empty
            if self.LOKI_TS_PREFIX.match(line)
        )

        if loki_prefix_count < len(non_empty) * 0.3:
            return None

        # Среди Loki-prefixed строк проверяем nginx access/error формат
        loki_lines = [line for line in non_empty if self.LOKI_TS_PREFIX.match(line)]
        nginx_count = sum(
            1 for line in loki_lines
            if self.LOKI_NGINX_ACCESS.match(line) or self.LOKI_NGINX_ERROR.match(line)
        )

        if nginx_count >= len(loki_lines) * 0.5:
            return LogFormat.LOKI_NGINX

        return None

    def has_header(self, data: str, delimiter: str = ",") -> bool:
        """
        Проверяет, есть ли у CSV-данных заголовок.

        Args:
            data: Входные данные
            delimiter: Разделитель

        Returns:
            True если есть заголовок
        """
        lines = data.strip().split("\n")
        if len(lines) < 2:
            return False

        try:
            reader = csv.reader(io.StringIO("\n".join(lines[:5])), delimiter=delimiter)
            rows = list(reader)
            if len(rows) < 2:
                return False

            header = rows[0]
            # Заголовок обычно содержит текст, а не числа
            text_fields = sum(
                1 for field in header
                if field.strip() and not field.strip().replace(".", "").replace("-", "").isdigit()
            )
            return text_fields > len(header) * 0.5
        except csv.Error:
            return False
