# CLAUDE.md — правила для Claude Code

> **СТОП. Прочитай это до любых действий.**
>
> Этот проект живёт **только на GitHub**: `derb1rd/logzilla-studio-next`.
> Локальная папка — не источник истины. Не смотри в неё, не читай оттуда файлы.
>
> **Всё через `gh`:**
> - Читать файл: `gh api "repos/derb1rd/logzilla-studio-next/contents/<path>" -q ".content" | base64 -d`
> - Записать/закоммитить: `gh api "repos/derb1rd/logzilla-studio-next/contents/<path>" -X PUT -f message="..." -f sha="<sha>" -f content="<base64>"`
> - PR: `gh pr create`
>
> Никаких `ls`, `cat`, `Read`, `Edit`, `Write` на локальный диск для файлов проекта.

---

## Продукт

**logZilla3000** — локальный веб-парсер логов. Репозиторий: `derb1rd/logzilla-studio-next`.

Название продукта везде: **logzilla3000** (строчные) или **logZilla3000** (в UI/заголовках).
Слово **studio** в коде не используется — если встречается, замени на `logzilla3000`.

---

## Версионирование — единственный источник правды

Версия приложения хранится **только в одном месте**:

```
app/__init__.py  →  __version__ = "X.Y.Z"
```

Все остальные места читают её оттуда (Python-импорт) или динамически через `/api/health`.

### Что делать при выпуске новой версии

1. **Поднять версию** в `app/__init__.py`:
   ```python
   __version__ = "X.Y.Z"
   ```

2. **Проверить, что в UI версия отображается корректно** — строка статуса в topbar
   читает `service` и `version` из `/api/health` динамически, пересборка не нужна.

3. **Проверить, что в экспортируемом JSON** поле `_logzilla.logzilla_version` совпадает
   с новой версией (`app/export_service.py` импортирует `__version__` из `app/__init__.py`).

4. **Закоммитить и тегнуть**:
   ```bash
   git add app/__init__.py
   git commit -m "chore: bump version to X.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

5. **GitHub Actions** (`release.yml`) автоматически соберёт `logzilla3000-vX.Y.Z.zip`
   и опубликует его в GitHub Releases.

### Куда НЕ нужно прописывать версию вручную

- `web/` — JS читает версию через `/api/health`
- `build_dist.sh` — имя zip формируется из тега (`GITHUB_REF_NAME`)
- `app/export_service.py` — импортирует `__version__`
- `app/server.py` — импортирует `__version__`

---

## Структура

```
app/                  бэкенд (Python, stdlib http.server)
  __init__.py         ← ЕДИНСТВЕННОЕ место с __version__
  contract.py         ParseRequest / ParseResult / ExportRequest
  parse_service.py    обёртка ядра
  export_service.py   сериализация; добавляет logzilla_version в _logzilla.*
  server.py           HTTP-слой: /api/parse · /api/export · /api/health
  logging_setup.py    JSON-логи + run_id
web/                  фронтенд: index.html, app.js, core.js, observability.js, styles.css
vendor/               logZilla3000 (ядро) + sqlparse (vendored)
tests/                контракт + сервисы + smoke.py
build_dist.sh         собирает dist/logzilla3000 (самодостаточная папка)
run.sh                запуск из исходников
.github/workflows/    ci.yml + release.yml (пуш тега → zip в Releases)
```

## Логгеры

- `logzilla3000.server` — HTTP-слой
- `logzilla3000.parse` — сервис парсинга

## Отладочный объект в браузере

`window.__logzilla3000Debug` — дамп action log, доступен из консоли.
