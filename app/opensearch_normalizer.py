"""Нормализатор OpenSearch-экспортов.

Проблема: OpenSearch Dashboards выгружает документы с оберткой _meta.
Парсер logZilla3000 разворачивает вложенный dict _meta в dotted-ключи:

  {"sql": "SELECT...", "_meta.event_original.sql": "SELECT...", "_meta.id": "abc"}

Нормализация (три шага, без потери данных):
  1. _meta.event_original.XXX → XXX  (оригинальные поля лога; поднять если нет на верхнем уровне)
  2. _meta.XXX → XXX                 (прочие _meta поля; служебный шум OS отбросить)
  3. @timestamp → timestamp, @version → drop  (ECS/Logstash артефакты)

Поддерживает оба формата входных данных:
  • Pattern A — _meta ещё не развёрнут: {"_meta": {"event_original": {...}}}
  • Pattern B — парсер уже развернул: {"_meta.event_original.sql": "..."}

Правило коллизий: top-level поля побеждают → event_original заполняет пробелы
→ прочие _meta заполняют оставшиеся пробелы.

Идемпотентен: не трогает записи без _meta-ключей (early return).
"""

from __future__ import annotations

# Служебные поля OpenSearch/ES уровня _meta — не несут логической ценности:
#   id     — внутренний doc-id OpenSearch
#   index  — имя индекса (техническая метка хранения)
#   score  — релевантность поиска (нет смысла в логах)
#   type   — устаревший тип документа ES (убран в ES8/OS2)
#   version — версия документа в индексе
#   tags   — метки конвейера Logstash/Vector (не из приложения)
#   p      — флаг частичного лога Fluent Bit (_p)
_OS_META_NOISE: frozenset[str] = frozenset({
    "id", "index", "score", "type", "version", "tags", "p", "_p",
})

_PREFIX_EO = "_meta.event_original."  # len = 22
_PREFIX_META = "_meta."               # len = 6


def normalize_record(record: dict) -> dict:
    """Нормализует одну запись из OpenSearch-экспорта.

    Возвращает новый dict; исходный не изменяется.
    """
    has_dotted = any(k.startswith(_PREFIX_META) for k in record)
    has_nested = "_meta" in record and isinstance(record.get("_meta"), dict)
    has_at = "@timestamp" in record or "@version" in record

    if not has_dotted and not has_nested and not has_at:
        return record  # не OS-формат — быстрый возврат без аллокаций

    result: dict = {}

    # Шаг 1: все верхнеуровневые поля (кроме _meta и @-артефактов)
    for k, v in record.items():
        if not k.startswith(_PREFIX_META) and k != "_meta" and not k.startswith("@"):
            result[k] = v

    # Шаг 2: собираем eo_fields и meta_fields из dotted-ключей
    eo_fields: dict = {}
    meta_fields: dict = {}

    for k, v in record.items():
        if k.startswith(_PREFIX_EO):
            plain = k[len(_PREFIX_EO):]
            if plain:
                eo_fields[plain] = v
        elif k.startswith(_PREFIX_META):
            plain = k[len(_PREFIX_META):]
            # не брать event_original как целый объект/строку — уже разобрали выше
            if plain and plain != "event_original" and plain not in _OS_META_NOISE:
                meta_fields[plain] = v

    # Шаг 2б: вложенный _meta (Pattern A — парсер не развернул)
    nested_meta = record.get("_meta")
    if isinstance(nested_meta, dict):
        for mk, mv in nested_meta.items():
            if mk == "event_original" and isinstance(mv, dict):
                for ek, ev in mv.items():
                    if ek not in eo_fields:
                        eo_fields[ek] = ev
            elif mk not in _OS_META_NOISE and mk not in meta_fields:
                meta_fields[mk] = mv

    # Шаг 3: поднимаем event_original (заполняют пробелы в top-level)
    for k, v in eo_fields.items():
        if k not in result:
            result[k] = v

    # Шаг 4: поднимаем остаток _meta (заполняют оставшиеся пробелы)
    for k, v in meta_fields.items():
        if k not in result:
            result[k] = v

    # Шаг 5: @timestamp → timestamp (ECS)
    ts = record.get("@timestamp")
    if ts is not None and "timestamp" not in result:
        result["timestamp"] = ts

    return result


def normalize(records: list[dict]) -> list[dict]:
    """Нормализует список записей. Безопасен для не-OS данных."""
    return [normalize_record(r) for r in records]
