#!/bin/sh
# Единственный сетевой шаг: подготовка пакетов для ОС/архитектуры текущего Python.
set -eu
export PIP_DISABLE_PIP_VERSION_CHECK=1
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
python3 -m venv .venv
mkdir -p wheelhouse
.venv/bin/python -m pip download --only-binary=:all: -r requirements.lock --dest wheelhouse
echo 'Пакеты сохранены. Теперь ./setup-offline.sh можно выполнять без интернета.'
