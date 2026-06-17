"""
Основной модуль универсального парсера логов.

Объединяет детектор формата, очиститель и конвертер
в единый фасадный интерфейс.
"""

import json
import logging
import os
from typing import Any, Optional, Union

from .cleaners import LogCleaner
from .detectors import FormatDetector, LogFormat
from .converters import JSONConverter
from .sql_formatter import format_sql_fields, unescape_sql_in_json
from .message_expander import expand_message_fields, deep_expand, group_infra_fields, strip_k8s_fields
from .text_parser import parse_generic_line, is_export_metadata

logger = logging.getLogger(__name__)


class UniversalLogParser:
    """
    Универсальный парсер логов.

    Автоматически определяет формат входных данных, очищает от мусора
    и преобразует в структурированный JSON.

    Примеры использования:

        # Простой парсинг (автоопределение формата)
        parser = UniversalLogParser()
        result = parser.parse_file("access.log")
        print(parser.to_json(result))

        # Парсинг CSV с параметрами
        parser = UniversalLogParser(delimiter=";")
        result = parser.parse_file("data.csv")

        # Парсинг с фильтрацией
        parser = UniversalLogParser(
            log_levels=["ERROR", "WARN"],
            remove_ansi=True,
            remove_duplicates=True,
        )
        result = parser.parse_file("app.log")

        # Парсинг текстовых логов с кастомным паттерном
        parser = UniversalLogParser(
            pattern=r'(?P<time>\\d{4}-\\d{2}-\\d{2})\\s+(?P<level>\\w+)\\s+(?P<msg>.*)'
        )
        result = parser.parse_file("custom.log")
    """

    # Соответствие «расширение файла → формат» для parse_file/parse_text.
    # Единый источник истины: сервисный слой (studio) переиспользует эту таблицу,
    # чтобы зеркалить выбор формата, а не держать собственную копию.
    FORMAT_BY_EXT: dict[str, LogFormat] = {
        ".csv": LogFormat.CSV,
        ".tsv": LogFormat.TSV,
        ".json": LogFormat.JSON,
        ".jsonl": LogFormat.JSONL,
        ".ndjson": LogFormat.JSONL,
    }

    def __init__(
        self,
        delimiter: Optional[str] = None,
        encoding: str = "utf-8",
        # Параметры очистки
        remove_ansi: bool = True,
        remove_html: bool = True,
        remove_duplicates: bool = True,
        strip_lines: bool = True,
        normalize_whitespace: bool = True,
        remove_empty_lines: bool = True,
        custom_garbage_patterns: Optional[list[str]] = None,
        keep_patterns: Optional[list[str]] = None,
        # Параметры фильтрации
        log_levels: Optional[list[str]] = None,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
        # Параметры конвертации
        pattern: Optional[str] = None,
        field_names: Optional[list[str]] = None,
        header: Optional[list[str]] = None,
        has_header: Optional[bool] = None,
        normalize_keys: bool = True,
        coerce_types: bool = True,
        skip_null: bool = False,
        # Параметры вывода
        indent: int = 2,
        ensure_ascii: bool = False,
        # SQL-форматирование
        format_sql: bool = True,
        # Раскрытие вложенных JSON в message
        expand_message: bool = True,
        # Удаление k8s-инфраструктурного шума (pod/container/labels/docker)
        strip_k8s: bool = False,
    ):
        """
        Инициализация универсального парсера логов.

        Args:
            delimiter: Разделитель для CSV (None = автоопределение)
            encoding: Кодировка входных файлов
            remove_ansi: Удалять ANSI escape-последовательности
            remove_html: Удалять HTML-теги
            remove_duplicates: Удалять дублирующиеся строки
            strip_lines: Удалять пробелы в начале/конце строк
            normalize_whitespace: Нормализовать пробелы
            remove_empty_lines: Удалять пустые строки
            custom_garbage_patterns: Дополнительные regex для удаления мусора
            keep_patterns: Regex для строк, которые НЕ нужно удалять
            log_levels: Фильтр по уровням логирования
            date_start: Начальная дата для фильтрации (ISO формат)
            date_end: Конечная дата для фильтрации (ISO формат)
            pattern: Кастомный regex-паттерн для парсинга текстовых логов
            field_names: Имена полей для кастомного паттерна
            header: Имена колонок для CSV (если нет заголовка в файле)
            has_header: Есть ли заголовок в CSV (None = автоопределение)
            normalize_keys: Нормализовать ключи JSON
            coerce_types: Автоматически приводить типы данных
            skip_null: Пропускать поля со значением None
            indent: Отступы в JSON
            ensure_ascii: Экранировать не-ASCII символы
            format_sql: Форматировать SQL-запросы в полях sql/query/statement
            expand_message: Раскрывать вложенные JSON/Python-dict в поле message
        """
        self.encoding = encoding
        self.delimiter = delimiter
        self.pattern = pattern
        self.field_names = field_names
        self.header = header
        self.has_header = has_header

        # Инициализация компонентов
        self.cleaner = LogCleaner(
            remove_ansi=remove_ansi,
            remove_html=remove_html,
            remove_duplicates=remove_duplicates,
            strip_lines=strip_lines,
            normalize_whitespace=normalize_whitespace,
            remove_empty_lines=remove_empty_lines,
            custom_garbage_patterns=custom_garbage_patterns,
            keep_patterns=keep_patterns,
        )

        self.detector = FormatDetector()
        self.converter = JSONConverter(
            ensure_ascii=ensure_ascii,
            indent=indent,
            normalize_keys=normalize_keys,
            coerce_types=coerce_types,
            skip_null=skip_null,
        )

        # Параметры фильтрации
        self.log_levels = log_levels
        self.date_start = date_start
        self.date_end = date_end

        # SQL-форматирование
        self.format_sql = format_sql

        # Раскрытие вложенных JSON в message
        self.expand_message = expand_message

        # Удаление k8s-инфраструктурного шума
        self.strip_k8s = strip_k8s

    def parse(self, data: str) -> list[dict[str, Any]] | dict[str, Any]:
        """
        Парсинг строковых лог-данных.

        Автоматически определяет формат, очищает данные
        и преобразует в структурированный JSON.

        Args:
            data: Сырые лог-данные в виде строки

        Returns:
            Список словарей (для табличных данных) или
            словарь/список (для JSON-данных)
        """
        # 1. Определение формата по щадяще-очищенным данным (BOM/нули/control).
        #    Детектим ДО полной очистки: она схлопывает пробелы/делает unescape и
        #    может исказить структуру (и сорвать определение CSV со встроенным JSON).
        probe = self.cleaner.clean_json_content(data)
        fmt = self.detector.detect(probe)
        logger.debug("Определён формат: %s", fmt.value)

        # 1b. Если явно задан has_header или header — принудительно CSV
        if self.has_header is not None or self.header is not None:
            if fmt not in (LogFormat.CSV, LogFormat.TSV):
                fmt = LogFormat.CSV
                logger.debug("Формат принудительно установлен как CSV (has_header/header)")

        # 2. Очистка под формат: для табличных — щадящая (не искажаем ячейки),
        #    для остальных — полная.
        if fmt in (LogFormat.CSV, LogFormat.TSV):
            cleaned = self.cleaner.clean_tabular(data)
        elif fmt in (LogFormat.JSON, LogFormat.JSONL):
            cleaned = self._clean_json_content(data)
        else:
            cleaned = self.cleaner.clean(data)
        logger.debug("Очистка завершена, длина: %d → %d", len(data), len(cleaned))

        # 3. Фильтрация
        cleaned = self._apply_filters(cleaned, fmt)

        # 4. Конвертация в JSON
        result = self._convert(cleaned, fmt)

        # 5. Распаковка JSON-колонок (CSV/TSV) + раскрытие вложенных JSON в message
        result = self._expand_json_columns(result, fmt)
        result = expand_message_fields(result, enabled=self.expand_message)
        # 5b. Рекурсивное вскрытие структуры: достаём JSON, зарытый в любой строковой
        #     колонке/поле (event.original, _source, вложенный JSON-в-строке).
        result = deep_expand(result, enabled=self.expand_message)
        # 5c. Инфраструктурные поля (k8s/docker/под/неймспейс/дубль original) →
        #     в _meta, чтобы наверху остался лог сервиса.
        result = group_infra_fields(result, enabled=self.expand_message)
        # 5d. Опциональное удаление k8s-шума из _meta (тогл strip_k8s).
        if self.strip_k8s:
            result = strip_k8s_fields(result)

        # 6. SQL-форматирование
        result = format_sql_fields(result, enabled=self.format_sql)

        return result

    def parse_file(self, filepath: str) -> list[dict[str, Any]] | dict[str, Any]:
        """
        Парсинг лог-файла.

        Формат определяется по расширению файла:
        - .csv, .tsv → CSV
        - .json → JSON
        - .jsonl, .ndjson → JSONL
        - .log, .txt, .syslog → автоопределение (Apache/Nginx/syslog/текст)

        Args:
            filepath: Путь к файлу логов

        Returns:
            Структурированные данные (list или dict)

        Raises:
            FileNotFoundError: Если файл не найден
        """
        # Пытаемся определить кодировку
        content = self._read_file(filepath)
        logger.debug("Файл прочитан: %s (%d символов)", filepath, len(content))
        return self.parse_text(content, filepath=filepath)

    def parse_text(
        self, content: str, filepath: Optional[str] = None
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Парсинг уже прочитанного содержимого.

        Если задан filepath с известным расширением (.csv/.tsv/.json/...), формат
        выбирается по нему — как в parse_file. Иначе формат определяется по
        содержимому — как в parse(). Вынесено из parse_file, чтобы вызывающий, уже
        прочитавший файл (напр. сервисный слой studio ради метрик/диагностики
        кодировки), не читал его второй раз.

        Args:
            content: Содержимое лога
            filepath: Исходный путь (используется только для выбора формата по
                расширению; чтения файла не происходит)
        """
        ext = os.path.splitext(filepath)[1].lower() if filepath else ""
        fmt = self.FORMAT_BY_EXT.get(ext)

        if fmt is None:
            # Неизвестное расширение (или inline) — автоопределение по содержимому.
            return self.parse(content)

        logger.debug("Формат по расширению %s: %s", ext, fmt.value)
        # Для JSON/JSONL — минимальная очистка (не ломаем структуру)
        if fmt in (LogFormat.JSON, LogFormat.JSONL):
            cleaned = self._clean_json_content(content)
        elif fmt in (LogFormat.CSV, LogFormat.TSV):
            # Щадящая очистка: не искажаем встроенный в колонки JSON/дампы.
            cleaned = self.cleaner.clean_tabular(content)
        else:
            cleaned = self.cleaner.clean(content)

        # Фильтрация (даже при определении формата по расширению)
        cleaned = self._apply_filters(cleaned, fmt)

        result = self._convert(cleaned, fmt)
        result = self._expand_json_columns(result, fmt)
        result = expand_message_fields(result, enabled=self.expand_message)
        result = deep_expand(result, enabled=self.expand_message)
        result = group_infra_fields(result, enabled=self.expand_message)
        if self.strip_k8s:
            result = strip_k8s_fields(result)
        result = format_sql_fields(result, enabled=self.format_sql)
        return result

    def parse_files(self, filepaths: list[str]) -> list[dict[str, Any]]:
        """
        Парсинг нескольких файлов логов.

        Args:
            filepaths: Список путей к файлам

        Returns:
            Список результатов парсинга для каждого файла
        """
        results = []
        for filepath in filepaths:
            try:
                result = self.parse_file(filepath)
                results.append({
                    "source": os.path.basename(filepath),
                    "status": "ok",
                    "data": result,
                })
            except Exception as e:
                results.append({
                    "source": os.path.basename(filepath),
                    "status": "error",
                    "error": str(e),
                })
        return results

    def to_json(self, data: Any) -> str:
        """
        Сериализация результата в JSON-строку.

        Если format_sql=True, заменяет \\n/\\t на реальные символы
        внутри SQL-полей для читаемости.

        Args:
            data: Результат парсинга

        Returns:
            JSON-строка
        """
        result = self.converter.to_json_string(data)
        if self.format_sql:
            result = unescape_sql_in_json(result)
        return result

    def to_json_file(self, data: Any, filepath: str) -> None:
        """
        Запись результата в JSON-файл.

        Если format_sql=True, заменяет \\n/\\t на реальные символы
        внутри SQL-полей для читаемости.

        Args:
            data: Результат парсинга
            filepath: Путь к выходному файлу
        """
        json_str = self.converter.to_json_string(data)
        if self.format_sql:
            json_str = unescape_sql_in_json(json_str)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.write("\n")

    def _apply_filters(self, cleaned: str, fmt: LogFormat) -> str:
        """Применение фильтров по уровню и дате к очищенным данным."""
        # Фильтрация построчно (для текстовых форматов)
        if fmt in (LogFormat.TEXT, LogFormat.APACHE, LogFormat.NGINX, LogFormat.SYSLOG, LogFormat.LOKI_NGINX, LogFormat.LOGFMT):
            lines = cleaned.split("\n")
            if self.log_levels:
                lines = self.cleaner.filter_by_level(lines, self.log_levels)
                logger.debug("Фильтрация по уровням %s: осталось %d строк", self.log_levels, len(lines))
            if self.date_start or self.date_end:
                lines = self.cleaner.filter_by_date_range(
                    lines, self.date_start, self.date_end
                )
                logger.debug("Фильтрация по дате: осталось %d строк", len(lines))
            cleaned = "\n".join(lines)

        # Фильтрация для табличных форматов (с сохранением заголовка)
        if fmt in (LogFormat.CSV, LogFormat.TSV):
            lines = cleaned.split("\n")
            if self.log_levels and len(lines) > 1:
                hdr = lines[0]
                data_lines = self.cleaner.filter_by_level(lines[1:], self.log_levels)
                lines = [hdr] + data_lines
                logger.debug("Фильтрация CSV по уровням: осталось %d строк", len(data_lines))
            if self.date_start or self.date_end:
                if len(lines) > 1:
                    hdr = lines[0]
                    data_lines = self.cleaner.filter_by_date_range(
                        lines[1:], self.date_start, self.date_end
                    )
                    lines = [hdr] + data_lines
                    logger.debug("Фильтрация CSV по дате: осталось %d строк", len(data_lines))
            cleaned = "\n".join(lines)

        return cleaned

    def _convert(self, cleaned: str, fmt: LogFormat) -> Any:
        """Конвертация очищенных данных в зависимости от формата."""
        if fmt == LogFormat.CSV or fmt == LogFormat.TSV:
            delimiter = self.delimiter or self.detector.detect_delimiter(cleaned)
            has_header = self.has_header
            if has_header is None:
                has_header = self.detector.has_header(cleaned, delimiter)
            return self.converter.convert_csv_to_json(
                cleaned,
                delimiter=delimiter,
                header=self.header,
                has_header=has_header,
            )

        elif fmt == LogFormat.JSON:
            return self.converter.convert_json_to_json(cleaned)

        elif fmt == LogFormat.JSONL:
            return self.converter.convert_jsonl_to_json(cleaned)

        elif fmt == LogFormat.APACHE:
            return self.converter.convert_apache_to_json(cleaned)

        elif fmt == LogFormat.NGINX:
            return self.converter.convert_nginx_to_json(cleaned)

        elif fmt == LogFormat.LOKI_NGINX:
            return self.converter.convert_loki_nginx_to_json(cleaned)

        elif fmt == LogFormat.SYSLOG:
            return self.converter.convert_syslog_to_json(cleaned)

        elif fmt == LogFormat.LOGFMT:
            return self.converter.convert_logfmt_to_json(cleaned)

        elif fmt == LogFormat.TEXT:
            if self.pattern:
                return self.converter.convert_text_to_json(
                    cleaned,
                    pattern=self.pattern,
                    field_names=self.field_names,
                )
            # Без паттерна — автоизвлечение полей через cleaner
            return self._convert_text_auto(cleaned)

        else:
            # UNKNOWN формат — возвращаем как есть
            return self._convert_text_auto(cleaned)

    def _convert_text_auto(self, cleaned: str) -> list[dict[str, Any]]:
        """Конвертация текста без паттерна — структурный разбор по «голове» строки.

        Для каждой строки пытаемся выделить timestamp/level/thread/logger и
        остаток как message (parse_generic_line). Если структуры нет —
        оборачиваем строку как {"message": line}.

        Раньше здесь работал extract_fields, который регэкспами выдёргивал любые
        IP/URL/3-значные числа: миллисекунды попадали в http_status, фрагменты
        {...} — в json_snippet. Это и был основной источник «плохого распарса».
        """
        lines = cleaned.split("\n")
        results: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Преамбула экспорта Loki/Grafana — не лог-запись, в предпросмотр не берём.
            if is_export_metadata(line):
                continue
            rec = parse_generic_line(line)
            results.append(rec if rec is not None else {"message": line})
        return results

    def _expand_json_columns(self, result: Any, fmt: LogFormat) -> Any:
        """Для CSV/TSV разворачивает колонки-JSON-строки в плоские поля.

        Включается тем же флагом, что и раскрытие message (expand_message):
        обе операции про «достать структуру из строки». Для не-табличных
        форматов — no-op.
        """
        if not self.expand_message:
            return result
        if fmt not in (LogFormat.CSV, LogFormat.TSV):
            return result
        if not isinstance(result, list):
            return result
        return self.converter.expand_json_columns(result)

    def _clean_json_content(self, content: str) -> str:
        """Минимальная очистка для JSON/JSONL — не ломает структуру."""
        return self.cleaner.clean_json_content(content)

    def _read_file(self, filepath: str) -> str:
        """Чтение файла с автоопределением кодировки."""
        # Сначала пробуем указанную кодировку
        try:
            with open(filepath, "r", encoding=self.encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            pass

        # Пробуем распространённые кодировки (latin-1 НЕ в цикле — это fallback)
        for enc in ("utf-8-sig", "cp1251", "koi8-r"):
            try:
                with open(filepath, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue

        # Последняя попытка — latin-1 (всегда работает, никогда не выбрасывает UnicodeDecodeError)
        with open(filepath, "r", encoding="latin-1") as f:
            return f.read()
