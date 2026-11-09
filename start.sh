#!/bin/sh
# Запускает установленное окружение без загрузок и обновлений пакетов.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
  echo 'Сначала выполните ./setup-offline.sh' >&2
  exit 1
fi
exec .venv/bin/python -m atlas.cli "$@"
