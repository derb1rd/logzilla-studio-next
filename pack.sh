#!/usr/bin/env bash
# pack.sh — собрать и запаковать бету logzilla-studio ОДНОЙ командой.
#
# Делает по шагам:
#   1) (опц.) проставляет версию в app/__init__.py — она же видна в шапке UI и /api/health;
#   2) вызывает build_dist.sh (самодостаточная папка dist/logzilla-studio, Вариант А);
#   3) пакует в releases/logzilla-studio-<версия>-<дата>.zip
#      (версия и дата в имени — чтобы сборки коллег не перетирали друг друга).
#
# Использование:
#   ./pack.sh                # взять текущую версию из app/__init__.py
#   ./pack.sh 0.2.0-beta1    # сначала проставить эту версию, потом собрать
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
INIT="app/__init__.py"

# --- версия: читаем/пишем __version__ в app/__init__.py через python (надёжнее sed) ---
read_version() {
  python3 - "$INIT" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', src)
print(m.group(1) if m else "")
PY
}

write_version() {
  python3 - "$INIT" "$1" <<'PY'
import re, sys
path, version = sys.argv[1], sys.argv[2]
src = open(path, encoding="utf-8").read()
new, n = re.subn(r'(__version__\s*=\s*)["\'][^"\']*["\']', r'\g<1>"%s"' % version, src, count=1)
if n == 0:
    sys.exit(f"✗ Не нашёл __version__ в {path}")
open(path, "w", encoding="utf-8").write(new)
PY
}

# 1) если передан аргумент — проставляем версию
if [ "$#" -ge 1 ]; then
  write_version "$1"
  echo "  версия → $1"
fi

VERSION="$(read_version)"
if [ -z "$VERSION" ]; then
  echo "✗ Не удалось определить версию из $INIT" >&2
  exit 1
fi
DATE="$(date +%Y%m%d)"

echo "→ Сборка беты logzilla-studio v$VERSION"

# 2) собрать самодостаточную папку (ядро + sqlparse вендорятся внутрь)
./build_dist.sh

# 3) запаковать
mkdir -p releases
ZIP="releases/logzilla-studio-${VERSION}-${DATE}.zip"
rm -f "$ZIP"
( cd dist && zip -rq "../$ZIP" logzilla-studio )

echo "✓ Готово: $ZIP"
echo "  Отдавайте этот zip. У получателя: распаковать → двойной клик «Запустить.command»."
