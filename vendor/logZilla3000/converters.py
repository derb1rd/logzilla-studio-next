"""
Модуль конвертации очищенных данных в JSON.

Обеспечивает структурированное преобразование данных
различных форматов в единый JSON-формат.
"""

import json
import csv
import io
import math
import re
from datetime import datetime
from typing import Any, Optional

# Строгие шаблоны числа для коэрции типов. Намеренно НЕ допускают подчёркивания:
# int()/float() трактуют '_' как разделитель разрядов, из-за чего id вроде
# '1779287397304638751_85e89394' разбирается как 85e89394 → переполнение в inf,
# а затем json.dumps пишет невалидный для строгих парсеров (браузер) 'Infinity'.
#
# Ведущие нули (007, 01234) НЕ коэрсятся в int: это индексы/индексы-маски,
# почтовые индексы, телефоны, ID — срезание нулей их искажает. Поэтому _INT_RE
# принимает либо одиночный '0', либо число без ведущего нуля. Дробная часть
# (0.5, 0.001) — легитимна и распознаётся _FLOAT_RE.
_INT_RE = re.compile(r"[+-]?(?:0|[1-9]\d*)$")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
# Ведущий ноль в float без дробной части — тоже идентификатор (0123.txt не сюда,
# но '00.5' → строка): float coerce только если нет ведущего 0 у целой части >1 цифры.
_FLOAT_LEADING_ZERO = re.compile(r"[+-]?0\d")


def _finite_float(token: str) -> Any:
    """parse_float для json.loads: не-финитные числа (1e999 → inf) → None."""
    f = float(token)
    return f if math.isfinite(f) else None


def _loads_finite(s: str) -> Any:
    """json.loads со страховкой строгого JSON на выходе: переполнения (1e999→inf)
    и литералы Infinity/-Infinity/NaN → None. Иначе на входе-JSON с такими
    значениями выход содержал бы inf/nan → невалидный для браузера/строгих парсеров
    JSON (и падение allow_nan=False при экспорте)."""
    return json.loads(s, parse_constant=lambda _c: None, parse_float=_finite_float)


# logfmt: key=value, значение опц. в кавычках. Дублирует паттерн детектора, чтобы
# конвертер не зависел от модуля detectors (избегаем циклического импорта).
_LOGFMT_PAIR = re.compile(
    r'([A-Za-z_][\w.\-]*)='
    r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s"\']*)'
)


def _nil(value: Optional[str]) -> Optional[str]:
    """NILVALUE RFC5424: '-' означает отсутствие значения → None."""
    return None if value == "-" else value


_LOGFMT_UNESCAPE = re.compile(r"\\(.)")
_LOGFMT_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


def _logfmt_unescape_repl(m: "re.Match") -> str:
    """\\n→перевод строки, \\t→таб, \\r→возврат; \\" \\\\ и прочее → сам символ."""
    ch = m.group(1)
    return _LOGFMT_ESCAPES.get(ch, ch)


class JSONConverter:
    """Конвертер данных в JSON с нормализацией и валидацией."""

    def __init__(
        self,
        ensure_ascii: bool = False,
        indent: int = 2,
        normalize_keys: bool = True,
        coerce_types: bool = True,
        skip_null: bool = False,
    ):
        """
        Инициализация конвертера.

        Args:
            ensure_ascii: Экранировать не-ASCII символы
            indent: Отступы в JSON (None для компактного формата)
            normalize_keys: Нормализовать ключи (нижний регистр, snake_case)
            coerce_types: Автоматически приводить типы данных
            skip_null: Пропускать поля со значением None
        """
        self.ensure_ascii = ensure_ascii
        self.indent = indent
        self.normalize_keys = normalize_keys
        self.coerce_types = coerce_types
        self.skip_null = skip_null

    def convert_csv_to_json(
        self,
        data: str,
        delimiter: str = ",",
        header: Optional[list[str]] = None,
        has_header: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Конвертация CSV-данных в список JSON-объектов.

        Args:
            data: CSV-данные в виде строки
            delimiter: Разделитель полей
            header: Список имён колонок (если нет заголовка в файле)
            has_header: Есть ли заголовок в данных

        Returns:
            Список словарей
        """
        reader = csv.reader(io.StringIO(data), delimiter=delimiter)
        rows = list(reader)

        if not rows:
            return []

        # Определяем заголовки
        if has_header and header is None:
            fieldnames = rows[0]
            data_rows = rows[1:]
        elif header is not None:
            fieldnames = header
            data_rows = rows
        else:
            # Генерируем имена колонок: col_0, col_1, ...
            max_cols = max(len(row) for row in rows)
            fieldnames = [f"col_{i}" for i in range(max_cols)]
            data_rows = rows

        # Нормализуем имена колонок
        if self.normalize_keys:
            fieldnames = [self._normalize_key(name) for name in fieldnames]

        def _cell(value: Optional[str]) -> Any:
            if value is not None:
                value = value.strip()
                if value == "":
                    value = None
            if self.coerce_types and value is not None:
                value = self._coerce_type(value)
            return value

        result = []
        for row in data_rows:
            row = self._repair_json_payload_row(row, fieldnames, delimiter)
            record = {}
            # Идём по максимуму из (заголовок, строка): если в строке полей БОЛЬШЕ,
            # чем колонок в заголовке (рваный CSV), лишние не теряем — кладём под
            # синтетическими именами col_N, а не отбрасываем молча.
            ncols = max(len(fieldnames), len(row))
            for i in range(ncols):
                fieldname = fieldnames[i] if i < len(fieldnames) else f"col_{i}"
                value = _cell(row[i] if i < len(row) else None)
                if self.skip_null and value is None:
                    continue
                record[fieldname] = value
            result.append(record)

        return result

    def _repair_json_payload_row(
        self, row: list[str], fieldnames: list[str], delimiter: str
    ) -> list[str]:
        """Чинит рваную строку CSV-экспорта вида «<префикс…>,<JSON-payload>».

        Табличные экспорты логов кладут весь payload последней колонкой JSON-строкой.
        Две беды бьют по разбору:
        1. Ведущая колонка содержит НЕзакавыченный разделитель (Kibana @timestamp
           «Jun 11, 2026 @ 12:02:14.345» с запятой) → csv.reader дробит её.
        2. Кривое экранирование внутри payload (битый sql и т.п.) рвёт сам JSON на
           несколько полей.
        В обоих случаях полей получается БОЛЬШЕ заголовка, а наивный разбор
        рассыпает запись на col_2…col_N. Здесь: находим, где начинается JSON
        (первое поле с '{'), склеиваем ВСЁ от него в одну payload-колонку, а лишние
        ведущие поля сливаем в первую колонку. Если payload-JSON валиден — он потом
        развернётся; если кривой (битый источник) — останется одной строкой, без
        мусорных col_N. Для нормальных строк (полей ≤ колонок) — no-op.
        """
        if len(fieldnames) < 1 or len(row) <= len(fieldnames):
            return row
        # JSON-payload — последняя колонка; ищем поле, с которого начинается '{',
        # но не раньше предпоследней колонки (иначе это не payload-хвост).
        jstart = next(
            (i for i, c in enumerate(row)
             if isinstance(c, str) and c.lstrip().startswith("{")),
            None,
        )
        if jstart is None or jstart < len(fieldnames) - 1:
            # Нет JSON-хвоста: возможно, рвануло только из-за запятой в ведущей
            # колонке, а последнее поле — целый JSON-объект. Тогда сливаем излишек
            # в первую колонку (см. случай 1 без расщепления payload).
            if self._as_json_object(row[-1]) is not None:
                surplus = len(row) - len(fieldnames)
                return [delimiter.join(row[: surplus + 1])] + row[surplus + 1:]
            return row
        payload = delimiter.join(row[jstart:])
        prefix = row[:jstart]
        npre = len(fieldnames) - 1  # колонок до payload-колонки
        if npre >= 1 and len(prefix) > npre:
            merge = len(prefix) - npre + 1
            prefix = [delimiter.join(prefix[:merge])] + prefix[merge:]
        return prefix + [payload]

    def convert_jsonl_to_json(self, data: str) -> list[dict[str, Any]]:
        """
        Конвертация JSON Lines в список JSON-объектов.

        Args:
            data: JSONL-данные (один JSON на строку)

        Returns:
            Список словарей
        """
        result = []
        for line in data.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = _loads_finite(line)
                if self.normalize_keys and isinstance(obj, dict):
                    obj = self._normalize_dict_keys(obj)
                result.append(obj)
            except (json.JSONDecodeError, ValueError):
                # Пропускаем невалидные строки
                continue
        return result

    def convert_json_to_json(self, data: str) -> Any:
        """
        Нормализация JSON-данных.

        Args:
            data: JSON-строка

        Returns:
            Нормализованный Python-объект
        """
        try:
            obj = _loads_finite(data)
        except (json.JSONDecodeError, ValueError):
            # Если не удалось распарсить как единый JSON,
            # пробуем как JSONL (по строкам)
            return self.convert_jsonl_to_json(data)

        if self.normalize_keys and isinstance(obj, dict):
            obj = self._normalize_dict_keys(obj)
        elif self.normalize_keys and isinstance(obj, list):
            obj = [
                self._normalize_dict_keys(item) if isinstance(item, dict) else item
                for item in obj
            ]

        return obj

    def convert_text_to_json(
        self,
        data: str,
        pattern: Optional[str] = None,
        field_names: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """
        Конвертация текстовых логов в JSON с помощью regex-паттерна.

        Args:
            data: Текстовые лог-данные
            pattern: Regex-паттерн с именованными группами
            field_names: Имена полей (если паттерн использует неименованные группы)

        Returns:
            Список словарей
        """
        lines = data.strip().split("\n")
        result = []

        if pattern:
            regex = re.compile(pattern)
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                match = regex.search(line)
                if match:
                    if field_names:
                        record = {
                            name: match.group(i + 1)
                            for i, name in enumerate(field_names)
                            if i + 1 <= len(match.groups())
                        }
                    else:
                        record = match.groupdict()

                    if self.coerce_types:
                        record = {
                            k: self._coerce_type(v) for k, v in record.items()
                        }

                    if self.normalize_keys:
                        record = {
                            self._normalize_key(k): v
                            for k, v in record.items()
                        }

                    result.append(record)
        else:
            # Без паттерна — каждая строка как отдельная запись
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                result.append({"message": line})

        return result

    def convert_apache_to_json(self, data: str) -> list[dict[str, Any]]:
        """
        Конвертация Apache Combined Log Format в JSON.

        Args:
            data: Логи Apache

        Returns:
            Список словарей
        """
        pattern = (
            r'(?P<ip>\S+)\s+'
            r'(?P<ident>\S+)\s+'
            r'(?P<user>\S+)\s+'
            r'\[(?P<timestamp>[^\]]+)\]\s+'
            r'"(?P<method>\S+)\s+(?P<path>\S+)\s+(?P<protocol>\S+)"\s+'
            r'(?P<status>\d{3})\s+'
            r'(?P<size>\d+|-)\s*'
            r'(?:\"(?P<referer>[^\"]*)\"\s+)?'
            r'(?:\"(?P<user_agent>[^\"]*)\")?'
        )
        return self.convert_text_to_json(data, pattern=pattern)

    def convert_nginx_to_json(self, data: str) -> list[dict[str, Any]]:
        """
        Конвертация Nginx Combined Log Format в JSON.

        Args:
            data: Логи Nginx

        Returns:
            Список словарей
        """
        pattern = (
            r'(?P<ip>\S+)\s+-\s+'
            r'(?P<user>\S+)\s+'
            r'\[(?P<timestamp>[^\]]+)\]\s+'
            r'"(?P<method>\S+)\s+(?P<path>\S+)\s+(?P<protocol>\S+)"\s+'
            r'(?P<status>\d{3})\s+'
            r'(?P<size>\d+)\s+'
            r'"(?P<referer>[^"]*)"\s+'
            r'"(?P<user_agent>[^"]*)"'
        )
        return self.convert_text_to_json(data, pattern=pattern)

    # RFC3164 (BSD) syslog: Jan 15 10:30:00 host app[pid]: message
    _SYSLOG_BSD = re.compile(
        r'^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+'
        r'(?P<host>\S+)\s+'
        r'(?P<app>\S+?)(?:\[(?P<pid>\d+)\])?:\s+'
        r'(?P<message>.*)$'
    )
    # rsyslog/systemd RFC3339-формат: ISO-TS HOST APP[pid]: MSG.
    # Требуем 'T' в таймстампе — это отделяет syslog от space-separated app-логов
    # (Java/Python пишут дату через пробел), и наличие 'host app:' структуры.
    _SYSLOG_ISO = re.compile(
        r'^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\S*)\s+'
        r'(?P<host>\S+)\s+'
        r'(?P<app>[\w./\-]+?)(?:\[(?P<pid>\d+)\])?:\s+'
        r'(?P<message>.*)$'
    )
    # RFC5424 syslog: <PRI>VER ISO-TS HOST APP PROCID MSGID [SD] MSG
    _SYSLOG_RFC5424 = re.compile(
        r'^<(?P<pri>\d{1,3})>(?P<version>\d{1,2})\s+'
        r'(?P<timestamp>-|\S+)\s+'
        r'(?P<host>\S+)\s+(?P<app>\S+)\s+(?P<pid>\S+)\s+(?P<msgid>\S+)\s+'
        r'(?P<rest>.*)$'
    )
    # Structured-data блок RFC5424 в начале MSG: [id k="v" ...][id2 ...]
    _SYSLOG_SD = re.compile(r'^(?:\[[^\]]*\]\s*)+')

    def convert_syslog_to_json(self, data: str) -> list[dict[str, Any]]:
        """
        Конвертация syslog в JSON. Поддерживает RFC3164 (BSD) и RFC5424.

        В RFC5424 из <PRI> вычисляются facility/severity, а "-" в полях
        timestamp/procid/msgid трактуется как NILVALUE → None.

        Args:
            data: Логи syslog

        Returns:
            Список словарей
        """
        result: list[dict[str, Any]] = []
        for line in data.split("\n"):
            line = line.strip()
            if not line:
                continue

            m = self._SYSLOG_RFC5424.match(line)
            if m:
                result.append(self._build_rfc5424(m))
                continue

            m = self._SYSLOG_ISO.match(line) or self._SYSLOG_BSD.match(line)
            if m:
                rec = m.groupdict()
                if rec.get("pid"):
                    rec["pid"] = int(rec["pid"])
                if self.normalize_keys:
                    rec = {self._normalize_key(k): v for k, v in rec.items()}
                result.append(rec)
                continue

            result.append({"message": line})

        return result

    def _build_rfc5424(self, m: "re.Match") -> dict[str, Any]:
        """Собирает запись из RFC5424-совпадения: PRI→facility/severity, SD→message."""
        pri = int(m.group("pri"))
        rec: dict[str, Any] = {
            "facility": pri >> 3,
            "severity": pri & 0x7,
            "version": int(m.group("version")),
            "timestamp": _nil(m.group("timestamp")),
            "host": _nil(m.group("host")),
            "app": _nil(m.group("app")),
            "pid": _nil(m.group("pid")),
            "msgid": _nil(m.group("msgid")),
        }
        rest = m.group("rest")
        sd = self._SYSLOG_SD.match(rest)
        if sd:
            rec["structured_data"] = sd.group(0).strip()
            rest = rest[sd.end():].lstrip()
        elif rest.startswith("- "):
            rest = rest[2:]
        elif rest == "-":
            rest = ""
        rec["message"] = rest
        if self.normalize_keys:
            rec = {self._normalize_key(k): v for k, v in rec.items()}
        return rec

    def convert_logfmt_to_json(self, data: str) -> list[dict[str, Any]]:
        """Конвертация logfmt (key=value пары) в список объектов.

        Каждая строка → объект из её пар. Значения снимаются с кавычек и
        приводятся к типам (coerce_types). Голый ключ без `=` (флаг) → True.
        """
        result: list[dict[str, Any]] = []
        for line in data.split("\n"):
            line = line.strip()
            if not line:
                continue
            record: dict[str, Any] = {}
            for m in _LOGFMT_PAIR.finditer(line):
                key, raw = m.group(1), m.group(2)
                if raw and raw[0] in "\"'" and raw[-1] == raw[0] and len(raw) >= 2:
                    inner = raw[1:-1]
                    # Снимаем экранирование вручную (\" \\ \n \t \r), НЕ через
                    # unicode_escape: тот через utf-8→latin-1 ломает кириллицу/эмодзи.
                    value: Any = _LOGFMT_UNESCAPE.sub(_logfmt_unescape_repl, inner) \
                        if "\\" in inner else inner
                elif raw == "":
                    value = None
                else:
                    value = self._coerce_type(raw) if self.coerce_types else raw
                nk = self._normalize_key(key) if self.normalize_keys else key
                record[nk] = value
            if record:
                result.append(record)
        return result

    def convert_loki_nginx_to_json(self, data: str) -> dict:
        """
        Конвертация Loki-prefixed ingress-nginx логов в JSON.

        Обрабатывает два типа строк:
        - Access-логи: loki_ts\\tIP - user [time] "method uri proto" status bytes "referer" "ua" ...
        - Error-логи: loki_ts\\tYYYY/MM/DD HH:MM:SS [level] pid#tid: *conn msg ...
        - Метаданные Loki: Common labels, Line limit, Total bytes processed

        Args:
            data: Логи ingress-nginx с Loki-префиксом

        Returns:
            Словарь с ключами metadata, access_logs, error_logs
        """
        # Паттерн для access-логов ingress-nginx
        access_pattern = re.compile(
            r'^(?P<loki_ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\t'
            r'(?P<remote_addr>\S+)\s+-\s+(?P<remote_user>\S+)\s+'
            r'\[(?P<time_local>[^\]]+)\]\s+'
            r'"(?P<method>\S+)\s+(?P<request_uri>[^\s"]+)\s+(?P<protocol>\S+)"\s+'
            r'(?P<status>\d{3})\s+(?P<body_bytes_sent>\d+)\s+'
            r'"(?P<http_referer>[^"]*)"\s+'
            r'"(?P<http_user_agent>[^"]*)"\s+'
            r'(?P<request_length>\d+)\s+(?P<request_time>[\d.]+)\s+'
            r'\[(?P<proxy_upstream_name>[^\]]*)\]\s+'
            r'\[(?P<upstream_addr_resolved>[^\]]*)\]\s+'
            r'(?P<upstream_addr>\S+)\s+'
            r'(?P<upstream_response_length>\d+)\s+'
            r'(?P<upstream_response_time>[\d.]+)\s+'
            r'(?P<upstream_status>\S+)\s+'
            r'(?P<request_id>\S+)'
        )

        # Паттерн для error-логов ingress-nginx
        error_pattern = re.compile(
            r'^(?P<loki_ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\t'
            r'(?P<error_time>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+'
            r'\[(?P<error_level>\w+)\]\s+'
            r'(?P<pid>\d+)#(?P<tid>\d+):\s+\*(?P<connection_id>\d+)\s+'
            r'(?P<error_message>.+?)'
            r'(?:,\s+client:\s+(?P<client>\S+))?'
            r'(?:,\s+server:\s+(?P<server>[^,]+))?'
            r'(?:,\s+request:\s+"(?P<request>[^"]*)")?'
            r'(?:,\s+upstream:\s+"(?P<upstream>[^"]*)")?'
            r'(?:,\s+host:\s+"(?P<host>[^"]*)")?'
            r'(?:,\s+referrer:\s+"(?P<referrer>[^"]*)")?'
            r'\s*$'
        )

        # Паттерн для метаданных Loki
        labels_pattern = re.compile(
            r'^Common labels:\s+(.+)$'
        )
        limit_pattern = re.compile(
            r'^Line limit:\s+"?([^"]+)"?$'
        )
        bytes_pattern = re.compile(
            r'^Total bytes processed:\s+"?([^"]+)"?$'
        )

        metadata = {}
        access_logs = []
        error_logs = []
        unmatched = []

        for line in data.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            # Проверяем метаданные
            m = labels_pattern.match(line)
            if m:
                try:
                    metadata["common_labels"] = json.loads(m.group(1))
                except json.JSONDecodeError:
                    metadata["common_labels"] = m.group(1)
                continue

            m = limit_pattern.match(line)
            if m:
                metadata["line_limit"] = m.group(1).strip()
                continue

            m = bytes_pattern.match(line)
            if m:
                metadata["total_bytes_processed"] = m.group(1).strip()
                continue

            # Проверяем access-лог
            m = access_pattern.match(line)
            if m:
                record = m.groupdict()
                # Приводим числовые поля
                for num_field in (
                    "status", "body_bytes_sent", "request_length",
                    "upstream_response_length",
                ):
                    if record.get(num_field):
                        record[num_field] = int(record[num_field])
                for float_field in ("request_time", "upstream_response_time"):
                    if record.get(float_field):
                        record[float_field] = float(record[float_field])
                # upstream_status может быть "-" при отсутствии upstream
                if record.get("upstream_status") and record["upstream_status"] != "-":
                    record["upstream_status"] = int(record["upstream_status"])
                # Очищаем пустые upstream_addr
                if record.get("upstream_addr_resolved") == "":
                    record["upstream_addr_resolved"] = None
                access_logs.append(record)
                continue

            # Проверяем error-лог
            m = error_pattern.match(line)
            if m:
                record = m.groupdict()
                if record.get("pid"):
                    record["pid"] = int(record["pid"])
                if record.get("tid"):
                    record["tid"] = int(record["tid"])
                if record.get("connection_id"):
                    record["connection_id"] = int(record["connection_id"])
                error_logs.append(record)
                continue

            # Неизвестная строка
            unmatched.append({"raw": line})

        result = {}
        if metadata:
            result["metadata"] = metadata
        if access_logs:
            result["access_logs"] = access_logs
        if error_logs:
            result["error_logs"] = error_logs
        if unmatched:
            result["unmatched"] = unmatched

        return result

    def to_json_string(self, data: Any) -> str:
        """
        Сериализация Python-объекта в JSON-строку.

        Args:
            data: Python-объект (list, dict и т.д.)

        Returns:
            JSON-строка
        """
        return json.dumps(
            data,
            ensure_ascii=self.ensure_ascii,
            indent=self.indent,
            default=self._json_default,
        )

    def to_json_file(self, data: Any, filepath: str) -> None:
        """
        Запись данных в JSON-файл.

        Args:
            data: Python-объект для сериализации
            filepath: Путь к выходному файлу
        """
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=self.ensure_ascii,
                indent=self.indent,
                default=self._json_default,
            )

    def expand_json_columns(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Разворачивает колонки, чьё значение — валидный JSON-объект, в плоские поля.

        Табличные экспорты часто кладут весь payload в одну колонку JSON-строкой
        (напр. event.original в Loki/k8s-выгрузках). Эта строка уже структурирована,
        поэтому вместо хранения её текстом мы парсим объект и поднимаем его ключи на
        верхний уровень записи. Вложенные объекты уплощаются через '_'
        (kubernetes.pod_name → kubernetes_pod_name), типы берутся из самого JSON.

        Колонки-скаляры и колонки, не являющиеся JSON-объектом, не трогаются.
        При совпадении имён значение из JSON перекрывает одноимённую колонку
        (источник богаче и типизирован). Порядок записей сохраняется.
        """
        result: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                result.append(record)
                continue
            flat: dict[str, Any] = {}
            json_objects: list[dict] = []
            # Два прохода, чтобы перекрытие было ДЕТЕРМИНИРОВАННЫМ (как обещает
            # докстринг): сначала кладём скалярные колонки, затем разворачиваем
            # JSON-колонки — их поля перекрывают одноимённые скаляры независимо от
            # порядка колонок в исходной строке.
            for key, value in record.items():
                obj = self._as_json_object(value)
                if obj is None:
                    flat[key] = value
                else:
                    json_objects.append(obj)
            for obj in json_objects:
                self._flatten_into(flat, obj, prefix="")
            result.append(flat)
        return result

    @staticmethod
    def _as_json_object(value: Any) -> Optional[dict]:
        """Возвращает dict, если value — строка с JSON-объектом, иначе None.

        Массивы/скаляры намеренно не разворачиваем: уплощать в верхний уровень
        осмысленно только объект (ключ→значение)."""
        if not isinstance(value, str):
            return None
        s = value.strip()
        if not s.startswith("{"):
            return None
        try:
            # parse_constant ловит литералы Infinity/-Infinity/NaN; _finite_float —
            # переполнения вроде 1e999. Всё не-финитное → None, чтобы на выходе был
            # строго валидный JSON (браузерный JSON.parse не принимает Infinity/NaN).
            obj = json.loads(s, parse_constant=lambda _c: None, parse_float=_finite_float)
        except (json.JSONDecodeError, ValueError):
            return None
        return obj if isinstance(obj, dict) else None

    def _flatten_into(self, dst: dict[str, Any], obj: dict, prefix: str) -> None:
        """Рекурсивно уплощает obj в dst, склеивая вложенные ключи через '_'."""
        for key, value in obj.items():
            nk = self._normalize_key(key)
            full = f"{prefix}_{nk}" if prefix else nk
            if isinstance(value, dict):
                self._flatten_into(dst, value, full)
            else:
                dst[full] = value

    def _normalize_key(self, key: str) -> str:
        """Нормализация ключа: нижний регистр, замена пробелов на _."""
        key = key.strip().lower()
        key = re.sub(r"[^\w]+", "_", key)
        key = re.sub(r"_+", "_", key)
        key = key.strip("_")
        return key

    def _normalize_dict_keys(self, obj: dict) -> dict:
        """Рекурсивная нормализация ключей словаря."""
        result = {}
        for key, value in obj.items():
            new_key = self._normalize_key(key)
            if isinstance(value, dict):
                result[new_key] = self._normalize_dict_keys(value)
            elif isinstance(value, list):
                result[new_key] = [
                    self._normalize_dict_keys(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[new_key] = value
        return result

    def _coerce_type(self, value: str) -> Any:
        """Автоматическое приведение типа строки.

        Примечание: строка "0" НЕ приводится к False, а "-" НЕ приводится
        к None, так как это легитимные значения в логах (код ответа 0,
        идентификаторы с дефисами и т.д.).
        """
        if not isinstance(value, str):
            return value

        # Пустая строка → None
        if value == "":
            return None

        # Булевы значения (только явные литералы, НЕ числа)
        if value.lower() in ("true", "yes", "on"):
            return True
        if value.lower() in ("false", "no", "off"):
            return False

        # Числа — только по строгому шаблону (без '_'-разделителей).
        if _INT_RE.match(value):
            try:
                return int(value)
            except ValueError:
                pass

        if _FLOAT_RE.match(value) and not _FLOAT_LEADING_ZERO.match(value):
            try:
                f = float(value)
                # Не-финитные (inf/nan, напр. из переполнения '1e999') не пускаем:
                # это невалидный JSON для строгих парсеров. Оставляем строкой.
                if math.isfinite(f):
                    return f
            except ValueError:
                pass

        # null (только явные литералы, НЕ дефис)
        if value.lower() in ("null", "none", "nil", "n/a"):
            return None

        return value

    @staticmethod
    def _json_default(obj: Any) -> Any:
        """Обработчик не-сериализуемых типов для JSON."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return str(obj)
