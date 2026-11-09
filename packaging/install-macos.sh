#!/bin/sh
# Replaces only the application bundle; user graphs/knowledge stay outside it.
set -eu
SOURCE=${1:-"$(dirname "$0")/Code Atlas.app"}
DESTINATION=${2:-"$HOME/Applications/Code Atlas.app"}
if [ ! -d "$SOURCE/Contents" ]; then echo 'Code Atlas.app not found' >&2; exit 1; fi
# Existing data must stay outside every replaceable application directory.
DATA_DIRECTORY=${ATLAS_DATA_DIR:-"$HOME/Library/Application Support/Code Atlas"}
if [ -d "$DATA_DIRECTORY" ]; then
  CANONICAL_DATA=$(CDPATH= cd -P "$DATA_DIRECTORY" && pwd)
  for APPLICATION_DIRECTORY in "$SOURCE" "$DESTINATION"; do
    if [ -d "$APPLICATION_DIRECTORY" ]; then
      CANONICAL_APP=$(CDPATH= cd -P "$APPLICATION_DIRECTORY" && pwd)
      case "$CANONICAL_DATA/" in
        "$CANONICAL_APP/"*) echo 'Move user data outside the application directory before updating.' >&2; exit 1 ;;
      esac
    fi
  done
fi
# Reject redirected or foreign bundles before moving anything into the application slot.
check_identity() {
  [ ! -L "$1" ] || { echo 'Refusing a symbolic-link application.' >&2; return 1; }
  APP_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist")
  [ "$APP_ID" = 'local.code-atlas.desktop' ] || { echo 'Unexpected application identity.' >&2; return 1; }
}
# A running MCP process may still use the old resources even after the GUI exits.
# lsof errors also refuse replacement; an unknown owner state is not an idle app.
check_inactive() {
  OWNER_LOG=$(mktemp /private/tmp/atlas-install-owners.XXXXXX)
  OWNER_STATUS=0
  OWNER_PIDS=$(/usr/sbin/lsof -t +D "$1" 2>"$OWNER_LOG") || OWNER_STATUS=$?
  if [ -n "$OWNER_PIDS" ] || [ -s "$OWNER_LOG" ] || [ "$OWNER_STATUS" -gt 1 ]; then
    rm -f "$OWNER_LOG"
    echo "Application still in use or ownership check failed: $1" >&2
    return 1
  fi
  rm -f "$OWNER_LOG"
}
check_identity "$SOURCE"
if [ -e "$DESTINATION" ] || [ -L "$DESTINATION" ]; then check_identity "$DESTINATION"; check_inactive "$DESTINATION"; fi
/usr/bin/codesign --verify --deep --strict "$SOURCE"
mkdir -p "$(dirname "$DESTINATION")"
STAGING="${DESTINATION%.app}.install-$$.app"
BACKUP="${DESTINATION%.app}.previous-$(date +%Y%m%d-%H%M%S)-$$.app"
if [ -e "$STAGING" ] || [ -L "$STAGING" ] || [ -e "$BACKUP" ] || [ -L "$BACKUP" ]; then echo 'Transaction path already exists.' >&2; exit 1; fi
trap 'if [ -d "$STAGING" ]; then rm -rf "$STAGING"; fi' EXIT
/usr/bin/ditto "$SOURCE" "$STAGING"
/usr/bin/codesign --verify --deep --strict "$STAGING"
"$STAGING/Contents/MacOS/CodeAtlas" --version
# A compatible binary update inspects data in place; migrations require their own backup.
"$STAGING/Contents/MacOS/CodeAtlas" --data-dir "$DATA_DIRECTORY" --prepare-update
if [ -e "$DESTINATION" ] || [ -L "$DESTINATION" ]; then check_identity "$DESTINATION"; check_inactive "$DESTINATION"; mv "$DESTINATION" "$BACKUP"; fi
if ! mv "$STAGING" "$DESTINATION"; then
  if [ -d "$BACKUP" ]; then mv "$BACKUP" "$DESTINATION"; fi
  exit 1
fi
# Check relocated code before committing retirement; a failure restores the old app.
if ! /usr/bin/codesign --verify --deep --strict "$DESTINATION"; then
  rm -rf "$DESTINATION"
  if [ -d "$BACKUP" ]; then mv "$BACKUP" "$DESTINATION"; fi
  exit 1
fi
if [ -d "$BACKUP" ]; then
  # This exact transaction-created app is obsolete; never remove stores/manual backup directories.
  PROTECTED=$(find "$BACKUP" \( -iname .atlas -o -iname .lancedb -o -iname agent-activity -o -iname data-schema.json -o -iname projects.json -o -iname appsettings.local.json -o -iname '*.lance' -o -iname '*.sqlite' -o -iname '*.sqlite3' -o -iname '*.db' \) -print -quit)
  if [ -z "$PROTECTED" ] && check_identity "$BACKUP" && check_inactive "$BACKUP"; then rm -rf "$BACKUP"; fi
fi
echo "Installed: $DESTINATION"
echo 'User data is preserved in ~/Library/Application Support/Code Atlas.'
if [ -d "$BACKUP" ]; then echo "Previous application retained because it contains protected data or is in use: $BACKUP"; fi
