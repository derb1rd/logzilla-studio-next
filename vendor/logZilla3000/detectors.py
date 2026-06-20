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

from .cleaners import reframe_tabular, _is_header_like
from .multiline_parser import is_python_traceback, is_go_panic, is_exception_group


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
    LOGFMT = "logfmt"  # key=value пары (Go/Grafana/Heroku/systemd)
    PYTHON_TRACEBACK = "python_traceback"  # Python traceback / Flask error block
    GO_PANIC = "go_panic"                  # Go goroutine dump with panic
    EXCEPTION_GROUP = "exception_group"    # Python ExceptionGroup / anyio TaskGroup
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

    # rsyslog/systemd RFC3339-syslog: ISO-TS(с 'T') HOST APP[pid]: MSG.
    # 'T' и структура 'host app:' отличают его от обычных app-логов с ISO-датой.
    SYSLOG_ISO = re.compile(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\S*\s+'
        r'\S+\s+[\w./\-]+(?:\[\d+\])?:\s+\S'
    )

    # RFC5424 syslog: <PRI>VERSION ISO-TIMESTAMP HOST APP PROCID MSGID ...
    # Пример: <34>1 2003-10-11T22:14:15.003Z mymachine.example.com su 1234 ID47 - msg
    SYSLOG_RFC5424 = re.compile(
        r'^<(\d{1,3})>\d{1,2}\s+'
        r'(?:-|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\S*)\s+'
        r'\S+\s+\S+\s+\S+\s+\S+'
    )

    # logfmt: пара key=value (значение опц. в кавычках). Используется и детектором,
    # и конвертером. Ключ — идентификатор; значение — "..."/'...'/токен без пробелов.
    LOGFMT_PAIR = re.compile(
        r'([A-Za-z_][\w.\-]*)='
        r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s"\']*)'
    )

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

    # Имя-метка с пробелами/пунктуацией: реальные экспорты сплошь такие
    # («Request Time», «Duration (ms)», «p95/p99», «error %»). Раньше такой
    # заголовок не матчился строгим паттерном → весь файл уезжал в текстовый путь.
    # Длину и число слов ограничиваем, чтобы прозу со случайными запятыми
    # («Hello, world, this is text») не принять за CSV.
    COLUMN_LABEL_PATTERN = re.compile(r"^@?[\"']?[A-Za-z_][\w .()\-/%:#@+]*[\"']?$")

    # Ячейка-«число или дата» — сильный признак таблицы (для guard'а ниже).
    _CELL_NUMBER = re.compile(r"^[+-]?(?:\d+(?:[.,]\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
    _CELL_DATE = re.compile(
        r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}|\d{1,2}:\d{2}:\d{2})"
    )

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

        # 0.5. Мультистрочные форматы: Python traceback, ExceptionGroup, Go panic.
        #      Проверяем ДО CSV — иначе трейсбеки с кавычками в путях (File "...") могут
        #      попасть в CSV (csv.reader интерпретирует " как кавычку поля).
        if is_exception_group(sample, stripped):
            return LogFormat.EXCEPTION_GROUP
        if is_python_traceback(sample, stripped):
            return LogFormat.PYTHON_TRACEBACK
        if is_go_panic(sample, stripped):
            return LogFormat.GO_PANIC

        # 1. Проверяем JSON / JSONL
        json_format = self._detect_json(sample)
        if json_format:
            return json_format

        # 2. Проверяем CSV / TSV на ОБРАМЛЁННОМ тексте (снятие over-quoting +
        #    пропуск преамбулы), чтобы обёрнутые/с-титулом экспорты не уезжали в
        #    текст. Сначала csv.reader на сыром тексте (устойчив к многострочным
        #    полям), затем — выборочная проверка как фолбэк.
        reframed = reframe_tabular(stripped)
        csv_format = self._detect_csv_raw(reframed) or self._detect_csv(
            reframed.split("\n")[: self.sample_size]
        )
        if csv_format:
            return csv_format
        # Обёрнутый/подпорченный экспорт: строгая проверка консистентности не прошла
        # (рваные внутренние строки — напр. незакавыченная запятая в timestamp), НО
        # рамка что-то размотала/срезала и первая строка — явная шапка. Тогда всё
        # равно CSV: конвертер разложит рваные строки по col_N, а вложенный JSON
        # развернётся. Гейт (reframed != stripped + шапка) не даёт ложных CSV из текста.
        if reframed != stripped:
            head = reframed.lstrip().split("\n", 1)[0]
            if _is_header_like(head):
                return LogFormat.CSV

        # 2.5. Проверяем Loki-prefixed nginx логи
        loki_nginx = self._detect_loki_nginx(sample)
        if loki_nginx:
            return loki_nginx

        # 3. Проверяем известные форматы (Nginx перед Apache, т.к. Apache паттерн тоже матчит Nginx).
        #    RFC5424-syslog проверяем отдельно: у него собственная грамматика с <PRI>.
        non_empty_sample = [l for l in (s.strip() for s in sample) if l]
        if non_empty_sample:
            rfc5424 = sum(1 for line in non_empty_sample if self.SYSLOG_RFC5424.match(line))
            if rfc5424 >= len(non_empty_sample) * 0.6:
                return LogFormat.SYSLOG
            iso_syslog = sum(1 for line in non_empty_sample if self.SYSLOG_ISO.match(line))
            if iso_syslog >= len(non_empty_sample) * 0.6:
                return LogFormat.SYSLOG

        check_order = [
            LogFormat.NGINX, LogFormat.APACHE, LogFormat.SYSLOG,
        ]
        for fmt in check_order:
            pattern = self.PATTERNS[fmt]
            matches = sum(1 for line in sample if pattern.match(line.strip()))
            if matches >= len(sample) * 0.6:  # 60% совпадений достаточно
                return fmt

        # 3.5. logfmt (key=value). Проверяем ПОСЛЕ табличных/web-форматов, но до
        #      эвристики «есть уровень → текст»: иначе строки logfmt с level=...
        #      ушли бы в text и потеряли структуру пар.
        if self._detect_logfmt(non_empty_sample):
            return LogFormat.LOGFMT

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
                ok, has_spacey = self._header_cells_ok(header)
                if not ok:
                    continue
                # Консистентность количества полей в data-строках
                data_rows = rows[1:]
                consistent = all(
                    len(row) == header_count or len(row) == header_count - 1
                    for row in data_rows
                )
                if not consistent:
                    continue
                # Заголовок с пробелами → требуем признак таблицы (число/дата),
                # иначе прозу со случайными запятыми приняли бы за CSV.
                if has_spacey and not self._looks_tabular(data_rows, header_count):
                    continue
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
            ok, has_spacey = self._header_cells_ok(header)
            if not ok:
                continue
            data_rows = [r for r in rows[1:] if r and any(c.strip() for c in r)]
            if not data_rows:
                continue
            # Консистентность числа полей (последняя колонка может быть пустой/опущенной)
            consistent = all(
                len(r) == header_count or len(r) == header_count - 1
                for r in data_rows
            )
            if not consistent:
                continue
            if has_spacey and not self._looks_tabular(data_rows, header_count):
                continue
            return fmt
        return None

    def _header_cells_ok(self, header: list) -> tuple[bool, bool]:
        """Валиден ли заголовок как набор имён-колонок.

        Возвращает (ok, has_spacey): ok — все ячейки выглядят как имена колонок;
        has_spacey — хотя бы одна потребовала «свободного» паттерна (есть пробел/
        пунктуация). Для has_spacey вызывающий потребует дополнительный признак
        таблицы, чтобы не спутать прозу с CSV.
        """
        has_spacey = False
        for raw in header:
            h = raw.strip()
            if not h:
                return False, has_spacey
            if self.COLUMN_NAME_PATTERN.match(h):
                continue
            if len(h) <= 60 and len(h.split()) <= 6 and self.COLUMN_LABEL_PATTERN.match(h):
                has_spacey = True
                continue
            return False, has_spacey
        return True, has_spacey

    def _looks_tabular(self, data_rows: list, ncols: int) -> bool:
        """Есть ли колонка, где большинство ячеек — число или дата.

        Это сильный сигнал «настоящей таблицы». Требуется только когда заголовок
        содержит пробелы (свободный паттерн) — чтобы текст со случайными запятыми
        не распознавался как CSV.
        """
        for i in range(ncols):
            col = [r[i].strip() for r in data_rows if i < len(r) and r[i].strip()]
            if not col:
                continue
            typed = sum(
                1 for c in col
                if self._CELL_NUMBER.match(c) or self._CELL_DATE.match(c)
            )
            if typed >= len(col) * 0.6:
                return True
        return False

    def _detect_logfmt(self, non_empty: list) -> bool:
        """logfmt-строка — это преимущественно пары key=value.

        Критерий: в большинстве строк ≥2 пары key=value, и пары покрывают
        существенную часть строки (а не одинокое foo=bar в прозе). Так мы
        ловим Go/Grafana/Heroku/systemd-логи, но не путаем с обычным текстом,
        где случайно затесалось `x=1`.
        """
        if not non_empty:
            return False

        good = 0
        for line in non_empty:
            # Дешёвый отсев: без '=' пар быть не может. Заодно защита от
            # катастрофического бэктрекинга findall на длинных строках без '='
            # (100k слов-символов → O(n²) на жадном [\w.\-]*=).
            if "=" not in line:
                continue
            pairs = self.LOGFMT_PAIR.findall(line)
            pairs = [(k, v) for k, v in pairs if k]
            if len(pairs) < 2:
                continue
            # Доля символов строки, покрытая парами key=value.
            covered = sum(len(k) + 1 + len(v) for k, v in pairs)
            if covered >= len(line.strip()) * 0.6:
                good += 1

        return good >= len(non_empty) * 0.6

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
