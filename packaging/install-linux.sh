#!/bin/sh
# Installs a native Linux onedir build per user; no sudo or data deletion.
set -eu
SOURCE=${1:-"$(dirname "$0")/CodeAtlas"}
DESTINATION=${2:-"$HOME/.local/opt/code-atlas"}
if [ ! -x "$SOURCE/CodeAtlas" ]; then echo 'Native Linux build not found' >&2; exit 1; fi
# Existing data must stay outside every replaceable application directory.
DATA_DIRECTORY=${ATLAS_DATA_DIR:-"${XDG_DATA_HOME:-$HOME/.local/share}/code-atlas"}
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
mkdir -p "$(dirname "$DESTINATION")" "$HOME/.local/bin" "$HOME/.local/share/applications"
STAGING="$DESTINATION.install-$$"
BACKUP="$DESTINATION.previous-$(date +%Y%m%d-%H%M%S)-$$"
trap 'if [ -d "$STAGING" ]; then rm -rf "$STAGING"; fi' EXIT
cp -R "$SOURCE" "$STAGING"
"$STAGING/CodeAtlas" --version
# A compatible binary update inspects data in place; migrations require their own backup.
"$STAGING/CodeAtlas" --data-dir "$DATA_DIRECTORY" --prepare-update
if [ -e "$DESTINATION" ]; then mv "$DESTINATION" "$BACKUP"; fi
if ! mv "$STAGING" "$DESTINATION"; then
  if [ -d "$BACKUP" ]; then mv "$BACKUP" "$DESTINATION"; fi
  exit 1
fi
ln -sfn "$DESTINATION/CodeAtlas" "$HOME/.local/bin/code-atlas"
cat > "$HOME/.local/share/applications/code-atlas.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Code Atlas
Exec="$DESTINATION/CodeAtlas" --desktop
Icon=$DESTINATION/_internal/packaging/assets/code-atlas.png
Terminal=false
Categories=Development;
EOF
echo "Installed: $DESTINATION; user data is preserved separately."
