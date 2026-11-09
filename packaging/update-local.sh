#!/bin/sh
# Build optionally, check the frozen runtime, then use the platform's staged installer.
# No external Python is needed when installing an already built application.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD=0
DRY_RUN=0
START=1
BUILD_PYTHON=${ATLAS_BUILD_PYTHON:-}
TARGET=${ATLAS_BUILD_TARGET:-}
SOURCE=
DESTINATION=
DATA=${ATLAS_DATA_DIR:-}

usage() {
  cat <<'EOF'
Usage: sh packaging/update-local.sh [options]
  --build                 Build current checkout before installation (native Python required)
  --python PATH           Build interpreter; otherwise select a matching local environment
  --target OS-CPU          Default: hardware-native target, including ARM64 under Rosetta
  --source DIRECTORY      Ready .app/onedir; incompatible with --build
  --destination DIRECTORY Override per-user application location
  --data-dir DIRECTORY    Existing separate data directory (also ATLAS_DATA_DIR)
  --no-start              Leave installed application stopped
  --dry-run               Print paths/actions only; no build, checks, install or launch
Close Atlas before updating. A running server or unknown data schema blocks installation.
EOF
}

fail() { echo "Update cancelled: $*" >&2; exit 1; }

# Resolve user paths before changing to the repository, keeping spaces literal.
absolute_path() {
  case "$1" in /*) printf '%s\n' "$1" ;; *) printf '%s/%s\n' "$PWD" "$1" ;; esac
}

# Resolve existing ancestors without mkdir, including macOS /var aliases and symlinks.
canonical_path() {
  if [ -d "$1" ]; then (CDPATH= cd -P -- "$1" && pwd); return; fi
  [ ! -e "$1" ] && [ ! -L "$1" ] || fail "Expected directory: $1"
  PARENT=$(canonical_path "$(dirname -- "$1")") || return 1
  case "$(basename -- "$1")" in
    .) printf '%s\n' "$PARENT" ;;
    ..) dirname -- "$PARENT" ;;
    *) printf '%s/%s\n' "${PARENT%/}" "$(basename -- "$1")" ;;
  esac
}

# Reject nested application/data roots, including not-yet-created directories.
separate_paths() {
  case "$1/" in "$2/"*) fail "Paths must be separate: $1 and $2" ;; esac
  case "$2/" in "$1/"*) fail "Paths must be separate: $1 and $2" ;; esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --build) BUILD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-start) START=0; shift ;;
    -h|--help) usage; exit 0 ;;
    --python|--target|--source|--destination|--data-dir)
      [ "$#" -ge 2 ] && [ -n "$2" ] || fail "Missing value for $1"
      case "$1" in
        --python) BUILD_PYTHON=$2 ;;
        --target) TARGET=$2 ;;
        --source) SOURCE=$(absolute_path "$2") ;;
        --destination) DESTINATION=$(absolute_path "$2") ;;
        --data-dir) DATA=$(absolute_path "$2") ;;
      esac
      shift 2 ;;
    *) fail "Unknown option: $1 (see --help)" ;;
  esac
done
[ "$BUILD" -eq 0 ] || [ -z "$SOURCE" ] || fail '--build cannot use --source'
[ -z "$DATA" ] || DATA=$(absolute_path "$DATA")
if [ -n "$BUILD_PYTHON" ] && [ -f "$BUILD_PYTHON" ]; then
  BUILD_PYTHON=$(absolute_path "$BUILD_PYTHON")
fi

SYSTEM=$(uname -s)
MACHINE=$(uname -m)
case "$SYSTEM" in
  Darwin)
    SYSTEM=darwin
    # Like build.py, use an already installed standalone CLT without switching Xcode globally.
    if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Library/Developer/CommandLineTools ]; then
      export DEVELOPER_DIR=/Library/Developer/CommandLineTools
    fi
    # uname describes the shell process under Rosetta; sysctl describes the hardware.
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" = 1 ]; then MACHINE=arm64; fi
    SOURCE=${SOURCE:-"$ROOT/dist/Code Atlas.app"}
    DESTINATION=${DESTINATION:-"$HOME/Applications/Code Atlas.app"}
    DATA=${DATA:-"$HOME/Library/Application Support/Code Atlas"}
    INSTALLER=install-macos.sh
    BINARY="$SOURCE/Contents/MacOS/CodeAtlas"
    ;;
  Linux)
    SYSTEM=linux
    SOURCE=${SOURCE:-"$ROOT/dist/CodeAtlas"}
    DESTINATION=${DESTINATION:-"$HOME/.local/opt/code-atlas"}
    DATA=${DATA:-"${XDG_DATA_HOME:-$HOME/.local/share}/code-atlas"}
    INSTALLER=install-linux.sh
    BINARY="$SOURCE/CodeAtlas"
    ;;
  *) fail 'Use update-local.ps1 on Windows; this script supports macOS and Linux.' ;;
esac
case "$MACHINE" in arm64|aarch64) MACHINE=arm64 ;; x86_64|amd64) MACHINE=x86_64 ;; *) fail "Unsupported CPU: $MACHINE" ;; esac
TARGET=${TARGET:-"$SYSTEM-$MACHINE"}
case "$TARGET" in
  macos-*) TARGET=darwin-${TARGET#macos-} ;;
  mac-*) TARGET=darwin-${TARGET#mac-} ;;
esac
case "$TARGET" in *-x64|*-amd64) TARGET=${TARGET%-*}-x86_64 ;; *-aarch64) TARGET=${TARGET%-*}-arm64 ;; esac
case "$TARGET" in "$SYSTEM-arm64"|"$SYSTEM-x86_64") ;; *) fail "Target must match this OS: $TARGET" ;; esac
CPU=${TARGET#*-}
SOURCE=$(canonical_path "$SOURCE")
DESTINATION=$(canonical_path "$DESTINATION")
DATA=$(canonical_path "$DATA")
case "$SYSTEM" in darwin) BINARY="$SOURCE/Contents/MacOS/CodeAtlas" ;; linux) BINARY="$SOURCE/CodeAtlas" ;; esac
separate_paths "$SOURCE" "$DESTINATION"
separate_paths "$SOURCE" "$DATA"
separate_paths "$DESTINATION" "$DATA"

printf 'Repository: %s\nTarget: %s\nSource: %s\nDestination: %s\nData (kept in place): %s\nBuild: %s; start: %s\n' \
  "$ROOT" "$TARGET" "$SOURCE" "$DESTINATION" "$DATA" "$BUILD" "$START"
if [ "$DRY_RUN" -eq 1 ]; then exit 0; fi
cd "$ROOT"

# Fail early instead of rebuilding with a Rosetta interpreter or installing dependencies silently.
matches_target() {
  "$1" -c 'import sys; sys.path.insert(0, "packaging"); from build_target import select_target; select_target(sys.argv[1])' "$TARGET" >/dev/null 2>&1
}

if [ "$BUILD" -eq 1 ]; then
  if [ -n "$BUILD_PYTHON" ]; then
    matches_target "$BUILD_PYTHON" || fail "Python does not match $TARGET: $BUILD_PYTHON"
  else
    for CANDIDATE in "$ROOT/build/arm64-runtime/.venv-arm64/bin/python" "$ROOT/.venv/bin/python" python3 python; do
      if matches_target "$CANDIDATE"; then BUILD_PYTHON=$CANDIDATE; break; fi
    done
    [ -n "$BUILD_PYTHON" ] || fail "No $TARGET Python found. See packaging/UPDATING.md; use --python PATH."
  fi
  echo "Build Python: $BUILD_PYTHON"
  "$BUILD_PYTHON" -m pip check
  "$BUILD_PYTHON" packaging/build.py --target "$TARGET"
fi

[ -x "$BINARY" ] || fail "Native executable not found: $BINARY"
# Validate actual binary headers even if a stale manifest claims another architecture.
if [ "$SYSTEM" = darwin ]; then
  lipo -verify_arch "$CPU" "$BINARY" || fail "Application is not $TARGET"
  codesign --verify --deep --strict "$SOURCE" || fail 'Application signature verification failed'
else
  DESCRIPTION=$(file -Lb "$BINARY")
  case "$CPU:$DESCRIPTION" in
    x86_64:*ELF\ 64-bit*x86-64*|arm64:*ELF\ 64-bit*ARM\ aarch64*) ;;
    *) fail "Application is not $TARGET: $DESCRIPTION" ;;
  esac
fi

export ATLAS_DATA_DIR="$DATA"
# Check lock/schema before the expensive native smoke and staging copy. Never kill unrelated MCP workers.
"$BINARY" --data-dir "$DATA" --prepare-update || fail 'Close Atlas and retry; data schema may require explicit migration.'
(unset PYTHONHOME PYTHONPATH; PATH='' "$BINARY" --native-document-check)
sh "$ROOT/packaging/$INSTALLER" "$SOURCE" "$DESTINATION"
if [ "$START" -eq 1 ]; then
  if [ "$SYSTEM" = darwin ]; then
    # Explicit arguments preserve a custom data root even when LaunchServices reuses its own environment.
    open -n "$DESTINATION" --args --desktop --data-dir "$DATA"
  else
    nohup "$DESTINATION/CodeAtlas" --desktop --data-dir "$DATA" >/dev/null 2>&1 </dev/null &
  fi
fi
echo 'Update complete. Previous application is retained by the installer; data was not copied or archived.'
