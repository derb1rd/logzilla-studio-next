"""Структурное логирование (JSON-строки) с run_id.

Наблюдаемость границы: каждое событие — это поля, а не проза, чтобы ИИ-агент
читал структуру, а не парсил текст. run_id связывает все события одного прогона.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Форматирует записи как одна JSON-строка на событие."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        # Произвольные структурные поля передаются через extra={"fields": {...}}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Логирует структурное событие: log_event(logger, 'parse_done', run_id=..., errors=3)."""
    logger.log(level, event, extra={"fields": fields})
