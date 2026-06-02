#!/usr/bin/env bash
# build_dist.sh — собирает САМОДОСТАТОЧНУЮ папку logzilla-studio (Вариант А).
#
# Результат: папка, которую можно «отдать» целиком. На машине получателя нужен
# только python3 (3.10+) — никакого pip/venv/сети/Homebrew. Ядро logZilla3000 и
# его единственная зависимость sqlparse вендорятся внутрь (vendor/).
#
# Сборка (на машине разработчика) требует python3 + доступ к ядру и к sqlparse
# (через установленный пакет или pip). Дев-репозиторий не засоряется: всё кладётся
# в dist/, копии в git не попадают.
#
# Использование:
#   ./build_dist.sh                 # → dist/logzilla-studio
#   ./build_dist.sh /путь/назначения
#   LOGZILLA3000_HOME=/путь/к/ядру ./build_dist.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DIST="${1:-$HERE/dist/logzilla-studio}"

# --- 1. Найти ядро --------------------------------------------------------
CORE="${LOGZILLA3000_HOME:-$HERE/../logzilla3000-project}"
if [ ! -f "$CORE/logZilla3000/__init__.py" ]; then
  echo "✗ Ядро logZilla3000 не найдено: $CORE" >&2
  echo "  Задайте путь: LOGZILLA3000_HOME=/путь/к/logzilla3000-project ./build_dist.sh" >&2
  exit 1
fi
CORE="$(cd "$CORE" && pwd)"

echo "→ Сборка дистрибутива"
echo "  studio: $HERE"
echo "  ядро:   $CORE"
echo "  цель:   $DIST"

rm -rf "$DIST"
mkdir -p "$DIST/vendor"

# --- 2. studio (app + web) ------------------------------------------------
rsync -a --exclude='__pycache__' "$HERE/app" "$DIST/"
rsync -a --exclude='__pycache__' "$HERE/web" "$DIST/"

# --- 3. Ядро (копируется как есть, без тестов и кэша) ---------------------
rsync -a --exclude='__pycache__' --exclude='tests' "$CORE/logZilla3000" "$DIST/vendor/"

# --- 4. sqlparse (pure-Python, ОПЦИОНАЛЬНАЯ зависимость) ------------------
# Ядро использует sqlparse лишь для «красивого» форматирования SQL и корректно
# деградирует без него (SQL остаётся как есть). Поэтому вендоринг best-effort:
# 1) копируем уже установленный пакет (без сети); 2) иначе пробуем pip; 3) иначе
# просто предупреждаем — папка остаётся полностью рабочей.
if SQLPARSE_DIR="$(python3 -c 'import os,sqlparse;print(os.path.dirname(sqlparse.__file__))' 2>/dev/null)"; then
  echo "  sqlparse: копирую из $SQLPARSE_DIR"
  rsync -a --exclude='__pycache__' "$SQLPARSE_DIR" "$DIST/vendor/"
elif command -v brew >/dev/null 2>&1 && \
     BREW_SQLPARSE="$(ls -d "$(brew --prefix sqlparse 2>/dev/null)"/libexec/lib/python*/site-packages/sqlparse 2>/dev/null | head -1)" && \
     [ -n "$BREW_SQLPARSE" ]; then
  echo "  sqlparse: копирую из brew ($BREW_SQLPARSE)"
  rsync -a --exclude='__pycache__' "$BREW_SQLPARSE" "$DIST/vendor/"
elif python3 -m pip install --quiet --target "$DIST/vendor" --no-compile "sqlparse>=0.4.0,<1.0.0" 2>/dev/null; then
  echo "  sqlparse: установлен через pip --target"
  rm -rf "$DIST/vendor"/*.dist-info "$DIST/vendor/bin" 2>/dev/null || true
else
  echo "  ⚠ sqlparse недоступен (нет локальной копии и сети) — SQL-форматирование"
  echo "    будет отключено. На работу парсера это не влияет. Чтобы включить:"
  echo "    положите пакет sqlparse в $DIST/vendor/ и пересоберите."
fi

# --- 5. Лаунчеры ----------------------------------------------------------
cat > "$DIST/run.sh" <<'EOF'
#!/usr/bin/env bash
# Запуск logzilla-studio из терминала. Нужен только python3 (всё остальное в vendor/).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
echo "logzilla-studio → http://${HOST}:${PORT}"
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

ЗАПУСК
  Двойной клик по «Запустить.command».
  Откроются окно терминала и браузер на http://127.0.0.1:8765.

ТРЕБОВАНИЯ
  • macOS с Python 3.10+ (проверка в терминале: python3 --version).
    Если Python нет — выполните: xcode-select --install
  Больше НИЧЕГО ставить не нужно: парсер и все зависимости уже внутри папки.

ЕСЛИ macOS СПРОСИТ «загружено из интернета»
  Правый клик по «Запустить.command» → «Открыть» → «Открыть».

ОСТАНОВИТЬ
  Закройте окно терминала (или Ctrl-C в нём).

ИЗ ТЕРМИНАЛА (альтернатива)
  ./run.sh                 # или другой порт: PORT=9000 ./run.sh
EOF

echo "✓ Готово: $DIST"
echo "  Отдавайте всю папку. Запуск у получателя: двойной клик по «Запустить.command»."
