#!/bin/sh
# Install the scanner dependencies in a fresh CI-only venv and read Git blobs.
set -eu

if [ "$#" -ne 5 ]; then
    echo 'Usage: run.sh REPOSITORY BASE_SHA HEAD_SHA OUTPUT_DIR VENV_DIR' >&2
    exit 2
fi

plugin_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
atlas_root=$(CDPATH='' cd -- "$plugin_dir/../.." && pwd)
cd "$atlas_root"

python3.12 -I -m venv "$5"
"$5/bin/python" -I -m pip --isolated --disable-pip-version-check install \
    --only-binary=:all: -r "$plugin_dir/requirements.txt"
exec "$5/bin/python" -E -s -B -m atlas.ci_health \
    --repo "$1" --base "$2" --head "$3" --output "$4"
