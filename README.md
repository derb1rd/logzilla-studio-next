# 🦎 logZilla Studio

Локальный парсер логов с веб-интерфейсом. Бросаешь файл логов (или вставляешь
текст) → получаешь разобранную таблицу с фильтрами, поиском и статистикой →
экспортируешь в JSON. Работает целиком на твоей машине, ничего никуда не
отправляет.

Под капотом — Python-ядро `logZilla3000` (распознаёт формат, разбирает строки),
а UI поверх него общается с ядром по простому HTTP/JSON. Зависимостей нет:
нужен только `python3` (3.10+), всё остальное лежит в `vendor/`.

---

## Запуск (для пользователя)

Ставить ничего не нужно — только `python3 --version` ≥ 3.10 (на macOS, если
Python нет: `xcode-select --install`).

1. Скачай свежий `logzilla-studio-vX.Y.Z.zip` со страницы
   **[Releases](../../releases)** и распакуй.
2. Запусти:
   - **двойной клик** по `Запустить.command` (откроет браузер сам), или
   - в терминале — `./run.sh`
3. Откроется `http://127.0.0.1:8765`. Останавливается по `Ctrl-C`.

> **macOS заблокировал «загружено из интернета»?** Это карантин Gatekeeper, он
> ставится при скачивании zip через браузер. Снимается одной командой в папке
> со сборкой: `xattr -dr com.apple.quarantine .`

## Как пользоваться

Перетащи файл логов (или вставь текст) → **Парсить** → смотри результат в
3-панельном инспекторе (список / детали записи / трасса) с фильтрами по уровню,
поиском и навигацией → **Экспортировать** в JSON (опционально gzip).

- **Предпросмотр** показывает до 10 000 строк (`PREVIEW_MAX`) — это полноценный
  рабочий просмотр, а не витрина. Если записей больше — увидишь плашку
  «показаны первые 10000 из N».
- **Экспорт** перепарсивает весь файл и отдаёт полный результат — сюда уходит
  работа с очень большими логами.

---

## Запуск из исходников (для разработки)

```bash
git clone https://github.com/derb1rd/logzilla-studio-next.git
cd logzilla-studio-next
./run.sh                 # → http://127.0.0.1:8765
PORT=9000 ./run.sh       # другой порт
NO_OPEN=1 ./run.sh       # не открывать браузер автоматически
```

Ядро `logZilla3000` и его зависимость `sqlparse` лежат в `vendor/` и
импортируются без установки. Для отладки против внешней копии ядра можно
переопределить путь: `export LOGZILLA3000_HOME=/путь/к/ядру`.

### Структура

```
app/                  бэкенд (Python, stdlib http.server)
  contract.py         контракт границы: ParseRequest / ParseResult / ExportRequest
  parse_service.py    обёртка ядра: единый тип + метрики + диагностика
  export_service.py   сериализация в JSON (+ опц. gzip)
  server.py           HTTP-слой: /api/parse · /api/export · /api/health · /api/client-log
  logging_setup.py    структурные JSON-логи + run_id
web/                  фронтенд (тёмная тема): index.html, app.js, core.js, styles.css
vendor/               завендоренные logZilla3000 + sqlparse (в git, в zip)
tests/                контракт + сервисы + golden-фикстуры + smoke.py
build_dist.sh         сборка самодостаточной папки/zip
```

Почему граница на HTTP/JSON, а не на JS-мосте — и дорожная карта UI: см.
[`REDESIGN.md`](./REDESIGN.md).

## API

```bash
# health
curl localhost:8765/api/health

# parse (inline)
curl -s localhost:8765/api/parse -H 'Content-Type: application/json' -d '{
  "source": {"kind":"inline","text":"2024-01-15 10:30:16 ERROR Database connection failed"},
  "options": {"log_levels":["ERROR","WARN"]},
  "preview": {"limit":1000,"offset":0}
}' | python3 -m json.tool

# export (несёт сам ParseRequest, парсит повторно → отдаёт JSON-файл; опц. gzip)
curl -s localhost:8765/api/export -H 'Content-Type: application/json' -d '{
  "parse_request": {"source":{"kind":"inline","text":"a,b\n1,2"}},
  "options": {"gzip": false}
}'
```

## Тесты

```bash
python3 tests/smoke.py             # без зависимостей (этот же тест гоняет CI)
python3 -m pytest tests/ -v        # полный набор, если установлен pytest
```

## Сборка и релиз

```bash
./build_dist.sh                    # → dist/logzilla-studio (самодостаточная папка)
```

Релиз для коллег собирается автоматически: пуш тега `vX.Y.Z` запускает
[`release.yml`](.github/workflows/release.yml) — CI собирает папку, зипует её и
кладёт zip + `SHA256SUMS` в GitHub Release.

```bash
git tag v0.2.0 && git push origin v0.2.0
```

## Отладка GUI с ИИ

GUI — главный источник недетерминированных багов, поэтому встроена наблюдаемость
границы:

- **action log** — `web/observability.js` ведёт журнал действий пользователя;
  кнопка «⛏ action log» в футере скачивает его JSON (готовый сценарий
  воспроизведения для ИИ-агента).
- **JS-ошибки** (`window.onerror`, `unhandledrejection`) уходят на
  `POST /api/client-log` и попадают в общий серверный лог.
- **Корреляция** — каждый запрос несёт `X-Correlation-Id` / `X-Session-Id`,
  сервер пишет их рядом с `run_id`.
- **PII-safe** — сервер логирует `input_sha1` + `input_len`, а не содержимое лога.

Петля: воспроизвести по action log → по `correlation_id` найти серверный
`run_id` и `input_sha1` → проверить ядро на том же `ParseRequest` (совпало → баг
в UI, нет → в ядре) → читать перехваченные JS-ошибки → чинить нужный слой.
