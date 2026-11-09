#!/bin/sh
# Восстанавливает окружение только из заранее подготовленного wheelhouse.
set -eu
export PIP_DISABLE_PIP_VERSION_CHECK=1
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Нужен Python 3.11 или новее"'
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements.lock
echo 'Готово. Запуск: ./start.sh --open'
