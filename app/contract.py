"""Контракт границы UI ↔ ядро.

Набор dataclass'ов с явной валидацией `from_dict` и сериализацией `to_dict`.
Слой намеренно framework-agnostic: те же типы будут работать и под FastAPI,
если позже понадобится auto-OpenAPI.

Поле `version` зарезервировано под версионирование схемы (пока не валидируется).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTRACT_VERSION = "1"

# Разумные пределы, чтобы UI/ядро не уронить большим вводом (MVP).
MAX_RECORDS = 100_000
# Окно предпросмотра. Раньше было 1000 («демонстрационная витрина»), но 3-pane
# инспектор (фильтры/поиск/навигация по трассе) дорос до полноценного рабочего
# просмотра — потолок поднят до 10k. Выше упираемся в нефиксированную высоту строк
# и невиртуализированный рендер потока (виртуализация — пункт #3 дорожной карты);
# полный результат сверх окна по-прежнему забирают экспортом.
PREVIEW_MAX = 10_000


class ContractError(ValueError):
    """Входной payload нарушает контракт. Возвращается клиенту как 400."""


# --------------------------------------------------------------------------- #
# Вспомогательные валидаторы
# --------------------------------------------------------------------------- #
def _require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"Поле '{name}' должно быть объектом, получено {type(value).__name__}")
    return value


def _as_bool(value: Any, name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(f"Поле '{name}' должно быть boolean")
    return value


def _as_str(value: Any, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ContractError(f"Поле '{name}' должно быть строкой")
    return value


def _as_int(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"Поле '{name}' должно быть целым числом")
    return value


# --------------------------------------------------------------------------- #
# Запрос
# --------------------------------------------------------------------------- #
@dataclass
class ParseOptions:
    """1:1 на параметры logZilla3000.UniversalLogParser."""

    encoding: str = "utf-8"
    log_levels: list[str] = field(default_factory=list)  # пустой список = все уровни
    remove_duplicates: bool = True
    remove_ansi: bool = True
    expand_message: bool = True
    compact_json: bool = False  # → indent=None
    format_sql: bool = True

    @classmethod
    def from_dict(cls, d: Any) -> "ParseOptions":
        d = _require_dict(d or {}, "options")
        levels = d.get("log_levels", [])
        if not isinstance(levels, list) or not all(isinstance(x, str) for x in levels):
            raise ContractError("Поле 'options.log_levels' должно быть списком строк")
        return cls(
            encoding=_as_str(d.get("encoding"), "options.encoding", "utf-8"),
            log_levels=[x.strip().upper() for x in levels if x.strip()],
            remove_duplicates=_as_bool(d.get("remove_duplicates"), "options.remove_duplicates", True),
            remove_ansi=_as_bool(d.get("remove_ansi"), "options.remove_ansi", True),
            expand_message=_as_bool(d.get("expand_message"), "options.expand_message", True),
            compact_json=_as_bool(d.get("compact_json"), "options.compact_json", False),
            format_sql=_as_bool(d.get("format_sql"), "options.format_sql", True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "encoding": self.encoding,
            "log_levels": self.log_levels,
            "remove_duplicates": self.remove_duplicates,
            "remove_ansi": self.remove_ansi,
            "expand_message": self.expand_message,
            "compact_json": self.compact_json,
            "format_sql": self.format_sql,
        }


@dataclass
class Source:
    kind: str  # "inline" | "file"
    text: str | None = None
    path: str | None = None

    @classmethod
    def from_dict(cls, d: Any) -> "Source":
        d = _require_dict(d, "source")
        kind = _as_str(d.get("kind"), "source.kind")
        if kind not in ("inline", "file"):
            raise ContractError("Поле 'source.kind' должно быть 'inline' или 'file'")
        if kind == "inline":
            text = d.get("text")
            if not isinstance(text, str):
                raise ContractError("Для source.kind='inline' требуется строковое 'source.text'")
            return cls(kind="inline", text=text)
        path = d.get("path")
        if not isinstance(path, str) or not path:
            raise ContractError("Для source.kind='file' требуется непустой 'source.path'")
        return cls(kind="file", path=path)


@dataclass
class Preview:
    limit: int = PREVIEW_MAX
    offset: int = 0

    @classmethod
    def from_dict(cls, d: Any) -> "Preview":
        d = _require_dict(d or {}, "preview")
        limit = _as_int(d.get("limit"), "preview.limit", PREVIEW_MAX)
        offset = _as_int(d.get("offset"), "preview.offset", 0)
        if limit < 1:
            raise ContractError("Поле 'preview.limit' должно быть ≥ 1")
        if offset < 0:
            raise ContractError("Поле 'preview.offset' должно быть ≥ 0")
        # Потолок окна предпросмотра (см. PREVIEW_MAX).
        return cls(limit=min(limit, PREVIEW_MAX), offset=offset)


@dataclass
class ParseRequest:
    source: Source
    options: ParseOptions = field(default_factory=ParseOptions)
    preview: Preview = field(default_factory=Preview)
    version: str = CONTRACT_VERSION

    @classmethod
    def from_dict(cls, d: Any) -> "ParseRequest":
        d = _require_dict(d, "ParseRequest")
        return cls(
            source=Source.from_dict(d.get("source")),
            options=ParseOptions.from_dict(d.get("options")),
            preview=Preview.from_dict(d.get("preview")),
            version=_as_str(d.get("version"), "version", CONTRACT_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        src: dict[str, Any] = {"kind": self.source.kind}
        if self.source.kind == "inline":
            src["text"] = self.source.text
        else:
            src["path"] = self.source.path
        return {
            "version": self.version,
            "source": src,
            "options": self.options.to_dict(),
            "preview": {"limit": self.preview.limit, "offset": self.preview.offset},
        }


# --------------------------------------------------------------------------- #
# Ответ
# --------------------------------------------------------------------------- #
@dataclass
class Metrics:
    """Прямой источник для плашек концепта (Всего/Отфильтровано/Ошибок/Предупр./Время)."""

    total_lines: int
    filtered: int
    errors: int
    warnings: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_lines": self.total_lines,
            "filtered": self.filtered,
            "errors": self.errors,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Diagnostic:
    level: str  # info | warn | error
    code: str
    message: str
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "code": self.code, "message": self.message, "line": self.line}


@dataclass
class ParseResult:
    status: str  # ok | partial | error
    format_detected: str | None
    metrics: Metrics
    records: list[dict[str, Any]]
    preview_window: dict[str, Any]
    diagnostics: list[Diagnostic]
    run_id: str
    version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "run_id": self.run_id,
            "format_detected": self.format_detected,
            "metrics": self.metrics.to_dict(),
            "records": self.records,
            "preview_window": self.preview_window,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


# --------------------------------------------------------------------------- #
# Экспорт
# --------------------------------------------------------------------------- #
@dataclass
class ExportOptions:
    gzip: bool = False
    ndjson: bool = False   # по объекту на строку (грепается/диффается лучше массива)

    @classmethod
    def from_dict(cls, d: Any) -> "ExportOptions":
        d = _require_dict(d or {}, "export.options")
        return cls(
            gzip=_as_bool(d.get("gzip"), "export.options.gzip", False),
            ndjson=_as_bool(d.get("ndjson"), "export.options.ndjson", False),
        )


@dataclass
class ExportRequest:
    """Stateless: экспорт повторно парсит тот же ParseRequest (единый источник истины).

    Выгрузка всегда JSON (формат не выбирается). gzip — опционально через options.
    """

    parse_request: ParseRequest
    options: ExportOptions = field(default_factory=ExportOptions)
    version: str = CONTRACT_VERSION

    @classmethod
    def from_dict(cls, d: Any) -> "ExportRequest":
        d = _require_dict(d, "ExportRequest")
        return cls(
            parse_request=ParseRequest.from_dict(d.get("parse_request")),
            options=ExportOptions.from_dict(d.get("options")),
            version=_as_str(d.get("version"), "version", CONTRACT_VERSION),
        )
