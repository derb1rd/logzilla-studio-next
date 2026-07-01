#!/usr/bin/env bash
# Запуск logzilla3000. Зависимостей нет — нужен только python3 3.10+.
set -euo pipefail
cd "$(dirname "$0")"

# Лог пишется в папку проекта — рядом с run.sh.
LOG="$(pwd)/logzilla_debug.log"
exec > >(tee -a "$LOG") 2>&1
echo ""
echo "=== logzilla3000 старт $(date) ==="

# Ищем Python 3.10+. Сначала проверяем python3 в PATH, затем стандартные пути Homebrew.
_find_python() {
  for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
      major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
      if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON=$(_find_python || true)
if [ -z "$PYTHON" ]; then
  echo ""
  echo "ОШИБКА: Python 3.10+ не найден."
  echo "Установи через Homebrew:"
  echo "  brew install python"
  echo "Затем открой новое окно терминала и запусти run.sh снова."
  echo ""
  echo "Найденные версии Python:"
  for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    command -v "$c" >/dev/null 2>&1 && echo "  $c: $($c --version 2>&1)" || true
  done
  exit 1
fi

echo "Python: $($PYTHON --version) → $PYTHON"

PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}"
echo "logzilla3000 → $URL"
echo "Лог: $LOG"
echo ""

# Открыть браузер, когда сервер поднимется (NO_OPEN=1 — отключить).
if [ "${NO_OPEN:-}" != "1" ] && command -v open >/dev/null 2>&1; then
  ( sleep 1; open "$URL" ) &
fi
exec "$PYTHON" -m app.server --host "$HOST" --port "$PORT"
