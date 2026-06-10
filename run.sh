#!/usr/bin/env bash
# Запуск logzilla-studio. Зависимостей нет — нужен только python3 (как и CLI ядра).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}"
echo "logzilla-studio → $URL"
# Открыть браузер, когда сервер поднимется (NO_OPEN=1 — отключить).
if [ "${NO_OPEN:-}" != "1" ] && command -v open >/dev/null 2>&1; then
  ( sleep 1; open "$URL" ) &
fi
exec python3 -m app.server --host "$HOST" --port "$PORT"
