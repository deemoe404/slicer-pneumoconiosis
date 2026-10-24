#!/bin/zsh
set -eu
MODULE_PROJECT="$(cd -- "$(dirname -- "$0")" && pwd)"
SLICER_APP="${SLICER_APP:-/Users/sam/Applications/3D Slicer 5.13.0 2026-10-22.app}"
mkdir -p "$MODULE_PROJECT/local-data"
exec "$SLICER_APP/Contents/MacOS/Slicer" \
  --disable-settings --ignore-slicerrc --no-splash \
  --additional-module-path "$MODULE_PROJECT/PneumoconiosisAnnotator" \
  --python-script "$MODULE_PROJECT/scripts/start.py" \
  >> "$MODULE_PROJECT/local-data/slicer.log" 2>&1
