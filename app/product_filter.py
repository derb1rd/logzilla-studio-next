"""Фильтр продуктовых полей VK Tax Compliance / Taxmonitor.

Принцип: белый список — пропускаем ТОЛЬКО поля из каталога продукта.
Всё остальное (инфраструктура, неизвестные поля) отбрасывается.

Источник каталога: taxcompliance_field_catalog.json (analysed 2026-06-18).
"""

from __future__ import annotations

from typing import Any

# Каталог продуктовых полей taxmon/taxcompliance.
PRODUCT_FIELDS: frozenset[str] = frozenset({
    # Пользователь / субъект
    "user", "user_id", "userid", "user_login", "user_ip", "userip",
    "login", "firstname", "middlename", "lastname", "groupid",
    "ernam", "subject",
    # Организация / налогоплательщик
    "org_unit", "org_unit_name", "taxpayer", "inn", "kpp", "oktmo",
    "ifns", "fts", "tax_inspection_code", "tax_authority_code",
    "tax_authority", "fts_name", "source_system",
    # Налоговый пакет / отчёт
    "tax_pack", "tax_pack_name", "taxform", "tax_type",
    "tax_period", "tax_period_name", "period", "fiscal_year",
    "corr_num", "correction_number", "version_reg",
    # Запрос / документ
    "ticket_id", "request_id", "id_transfer", "service_id",
    "object_type", "object_code", "object_name", "object_id",
    "doc_type", "doc_type_1c", "doc_id", "doc_date", "is_incoming",
    # Статус / аудит
    "action", "severity", "delegate", "labels", "resource", "status",
    # Лог-записи продукта
    "text", "recommendation", "comment", "created_at",
    "request_date", "response_date", "transfer_date", "erdat",
    # Мета-поля передачи (1C Connector)
    "transfer_taxforms", "taxform_rows_total", "taxform_batch_number",
    "taxform_batches_total", "taxform_batch_rows",
    # XML / файлы
    "xml_income_file", "xml_outcome_file", "file_name", "file_path",
    # Прочие продуктовые
    "description", "backtrace", "step", "process", "jwe_key_path",
    "requestid", "auditinfo",
    # Message / данные
    "message", "data", "payload", "body", "result",
    "error", "error_code", "error_message",
    "service", "stage_fields", "catalog_fields", "col_config",
})


def _filter_one(record: dict) -> dict:
    return {k: v for k, v in record.items() if k.lower() in PRODUCT_FIELDS}


def filter_records(data: Any) -> Any:
    """Рекурсивно оставляет только продуктовые поля из dict/list.

    Записи без продуктовых полей (чистая инфраструктура) из списка исключаются.
    """
    if isinstance(data, list):
        result = []
        for item in data:
            filtered = filter_records(item)
            if isinstance(filtered, dict) and not filtered:
                continue  # запись стала пустой — нет продуктовых полей, дропаем
            result.append(filtered)
        return result
    if isinstance(data, dict):
        filtered = _filter_one(data)
        return {
            k: filter_records(v) if isinstance(v, (dict, list)) else v
            for k, v in filtered.items()
        }
    return data
