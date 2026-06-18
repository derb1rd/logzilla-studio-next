#!/usr/bin/env bash
# build_dist.sh — собирает САМОДОСТАТОЧНУЮ папку logzilla-studio (Вариант А).
#
# Результат: папка, которую можно «отдать» целиком. На машине получателя нужен
# только python3 (3.10+) — никакого pip/venv/сети/Homebrew. Ядро logZilla3000 и
# его опциональная зависимость sqlparse уже лежат в vendor/ этого репозитория —
# сборка просто копирует исходники как есть (dev-раскладка == дистрибутив).
#
# Дев-репозиторий не засоряется: всё кладётся в dist/, копии в git не попадают.
#
# Использование:
#   ./build_dist.sh                 # → dist/logzilla-studio
#   ./build_dist.sh /путь/назначения
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DIST="${1:-$HERE/dist/logzilla-studio}"

# --- 1. Проверить наличие завендоренного ядра -----------------------------
if [ ! -f "$HERE/vendor/logZilla3000/__init__.py" ]; then
  echo "✗ Завендоренное ядро не найдено: $HERE/vendor/logZilla3000" >&2
  echo "  Репозиторий повреждён — vendor/ должен содержать logZilla3000 (и sqlparse)." >&2
  exit 1
fi

echo "→ Сборка дистрибутива"
echo "  studio: $HERE"
echo "  цель:   $DIST"

rm -rf "$DIST"
mkdir -p "$DIST/vendor"

# --- 2. studio (app + web) ------------------------------------------------
rsync -a --exclude='__pycache__' "$HERE/app" "$DIST/"
rsync -a --exclude='__pycache__' "$HERE/web" "$DIST/"

# --- 3. vendor (ядро + sqlparse) — как есть, без тестов и кэша ------------
# Ядро использует sqlparse лишь для «красивого» форматирования SQL и корректно
# деградирует без него; если sqlparse в vendor/ нет — папка всё равно рабочая.
#
# Точки входа standalone-ядра (gui.py/cli.py/__main__.py) в раздачу НЕ кладём:
# studio их не импортирует (использует parser/detectors), а gui.py тянет
# незавендоренный tkinterdnd2 — в сборке это лишь мёртвый код со сломанной
# зависимостью. Исходник в vendor/ остаётся нетронутым.
rsync -a --exclude='__pycache__' --exclude='tests' \
      --exclude='gui.py' --exclude='cli.py' --exclude='__main__.py' \
      "$HERE/vendor/logZilla3000" "$DIST/vendor/"
if [ -d "$HERE/vendor/sqlparse" ]; then
  rsync -a --exclude='__pycache__' "$HERE/vendor/sqlparse" "$DIST/vendor/"
else
  echo "  ⚠ vendor/sqlparse отсутствует — SQL-форматирование будет отключено (парсер работает)."
fi

# --- 5. Лаунчеры ----------------------------------------------------------
cat > "$DIST/run.sh" <<'EOF'
#!/usr/bin/env bash
# Запуск logzilla-studio из терминала. Нужен только python3 (всё остальное в vendor/).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}"
echo "logzilla-studio → $URL"
if [ "${NO_OPEN:-}" != "1" ] && command -v open >/dev/null 2>&1; then
  ( sleep 1; open "$URL" ) &
fi
exec python3 -m app.server --host "$HOST" --port "$PORT"
EOF
chmod +x "$DIST/run.sh"

# .command — двойной клик в Finder: поднимает сервер и открывает браузер.
cat > "$DIST/Запустить.command" <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен Python 3.10+."
  echo "Установите Command Line Tools командой:  xcode-select --install"
  echo
  read -n 1 -s -r -p "Нажмите любую клавишу для выхода…"
  exit 1
fi

echo "logZilla Studio → $URL"
echo "(закройте это окно, чтобы остановить)"
( sleep 1; open "$URL" ) &
exec python3 -m app.server --host "$HOST" --port "$PORT"
EOF
chmod +x "$DIST/Запустить.command"

# --- 6. README ------------------------------------------------------------
cat > "$DIST/README.txt" <<'EOF'
logZilla Studio — локальный парсер логов (самодостаточная сборка)

ТРЕБОВАНИЯ
  • macOS с Python 3.10+ (проверка в терминале: python3 --version).
    Если Python нет — выполните: xcode-select --install
  Больше НИЧЕГО ставить не нужно: парсер и все зависимости уже внутри папки.

ЗАПУСК (рекомендуется — из терминала)
  ./run.sh                 # откроет браузер на http://127.0.0.1:8765
  PORT=9000 ./run.sh       # другой порт
  Остановить — Ctrl-C.

ЗАПУСК ДВОЙНЫМ КЛИКОМ
  Двойной клик по «Запустить.command» (откроет терминал и браузер).

ЕСЛИ macOS БЛОКИРУЕТ «загружено из интернета»
  Это карантин Gatekeeper — он ставится при скачивании ZIP через браузер.
  Снять одной командой (выполнить в терминале в этой папке):
      xattr -dr com.apple.quarantine .
  После этого запуск работает обычным способом.
  (В macOS 15 Sequoia старый трюк «правый клик → Открыть» больше не работает —
   используйте команду выше, либо клонируйте репозиторий через git: при
   git clone карантин не ставится вообще.)
EOF

echo "✓ Готово: $DIST"
echo "  Отдавайте папку или клонируйте репозиторий. Запуск у получателя: ./run.sh"
