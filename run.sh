#!/usr/bin/env bash
# Запуск logzilla-studio. Зависимостей нет — нужен только python3 (как и CLI ядра).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
echo "logzilla-studio → http://${HOST}:${PORT}"
exec python3 -m app.server --host "$HOST" --port "$PORT"
