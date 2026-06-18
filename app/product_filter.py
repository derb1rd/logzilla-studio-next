"""Фильтр продуктовых полей VK Tax Compliance / Taxmonitor.

Принцип: блок-лист — удаляем гарантированно инфраструктурные поля
(zerolog, K8s, HTTP-middleware, nginx, SQL-логгер). Всё остальное остаётся.
Записи, из которых после фильтра ничего не осталось, исключаются.

Источник каталога: taxcompliance_field_catalog.json (analysed 2026-06-18).
"""

from __future__ import annotations

from typing import Any

_BASE_LOG: frozenset[str] = frozenset({
    "time", "ts", "level", "msg", "caller",
    "asctime", "created", "msecs", "relativecreated",
    "levelno", "levelname", "name", "module", "funcname", "lineno",
    "thread", "threadname", "process", "processname", "pathname", "filename",
    "logger_name", "thread_id", "thread_priority", "source_class", "source_method",
})

_OBSERVABILITY: frozenset[str] = frozenset({
    "category", "zone", "node", "service_name", "app",
    "service_instance", "service_version", "namespace",
    "trace_id", "span_id", "traceparent", "tracestate", "b3_traceid",
    "sentry_id",
})

_HTTP_MIDDLEWARE: frozenset[str] = frozenset({
    "req_id", "reqid", "method", "httpmethod",
    "url", "path", "remote_addr", "remoteaddr", "host",
    "status", "httpstatus", "request_time", "duration", "elapsed",
    "grpcfunction", "grpcerror", "grpc_method", "grpc_service", "grpc_code",
    "metadata", "user_agent", "referer", "protocol",
    "request_length", "response_length", "content_type",
})

_SQL: frozenset[str] = frozenset({
    "sql", "args", "rowcount", "rows_affected",
})

_NGINX: frozenset[str] = frozenset({
    "remote_user", "body_bytes_sent", "http_referer", "http_user_agent",
    "upstream_addr", "upstream_status", "upstream_response_time",
    "upstream_response_length", "proxy_upstream_name", "upstream_addr_resolved",
    "request_uri", "time_local", "connection_id", "server",
    "loki_ts", "error_level", "pid", "tid", "error_message", "error_time",
})

ALL_INFRASTRUCTURE: frozenset[str] = frozenset().union(
    _BASE_LOG, _OBSERVABILITY, _HTTP_MIDDLEWARE, _SQL, _NGINX,
)


def _filter_one(record: dict) -> dict:
    return {k: v for k, v in record.items() if k.lower() not in ALL_INFRASTRUCTURE}


def filter_records(data: Any) -> Any:
    """Рекурсивно удаляет инфраструктурные поля из dict/list.

    Записи, из которых после фильтра ничего не осталось, исключаются.
    """
    if isinstance(data, list):
        result = []
        for item in data:
            filtered = filter_records(item)
            if isinstance(filtered, dict) and not filtered:
                continue
            result.append(filtered)
        return result
    if isinstance(data, dict):
        filtered = _filter_one(data)
        return {
            k: filter_records(v) if isinstance(v, (dict, list)) else v
            for k, v in filtered.items()
        }
    return data
