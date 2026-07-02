#!/usr/bin/env bash
# Запуск logzilla3000. Зависимостей нет — нужен только python3 3.10+.
set -euo pipefail
cd "$(dirname "$0")"

# Лог пишется в папку проекта — рядом с run.sh.
LOG="$(pwd)/logzilla_debug.log"
exec > >(tee -a "$LOG") 2>&1
echo ""
echo "=== logzilla3000 старт $(date) ==="

# Ищем Python 3.10+. Порядок: python3 в PATH → стандартные пути Homebrew →
# python в PATH (так python.org ставит интерпретатор на Windows) →
# Python Launcher for Windows (py -3).
_check_version() {
  local major minor
  major=$("$@" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
  minor=$("$@" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
  [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]
}

_find_python() {
  local candidate
  for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && _check_version "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  if command -v py >/dev/null 2>&1 && _check_version py -3; then
    echo "py|-3"
    return 0
  fi
  return 1
}

PYTHON_RAW=$(_find_python || true)
if [ -z "$PYTHON_RAW" ]; then
  echo ""
  echo "ОШИБКА: Python 3.10+ не найден."
  echo "  macOS:   brew install python"
  echo "  Windows: скачай с python.org/downloads и отметь \"Add python.exe to PATH\""
  echo "Затем открой новое окно терминала и запусти run.sh снова."
  echo ""
  echo "Найденные версии Python:"
  for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python py; do
    command -v "$c" >/dev/null 2>&1 && echo "  $c: $($c --version 2>&1)" || true
  done
  exit 1
fi

IFS='|' read -r -a PYTHON <<< "$PYTHON_RAW"

echo "Python: $("${PYTHON[@]}" --version) → ${PYTHON[*]}"

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
exec "${PYTHON[@]}" -m app.server --host "$HOST" --port "$PORT"
