#!/bin/zsh
set -eu
MODULE_PROJECT="$(cd -- "$(dirname -- "$0")" && pwd)"
SLICER_APP="${SLICER_APP:-/Users/sam/Applications/3D Slicer 5.13.0 2026-10-22.app}"
mkdir -p "$MODULE_PROJECT/local-data"
MARKUPS_MODULES="$MODULE_PROJECT/local-data/extensions/ExtraMarkups/lib/Slicer-5.13/qt-loadable-modules"
if [[ ! -f "$MARKUPS_MODULES/libqSlicerShapeModule.dylib" ]]; then
  print '缺少圆形工具依赖，请先运行 .venv/bin/python scripts/prepare_markups.py。'
  exit 1
fi
export DYLD_LIBRARY_PATH="$MARKUPS_MODULES${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
cd "$MODULE_PROJECT/local-data"
exec "$SLICER_APP/Contents/MacOS/Slicer" \
  --disable-settings --ignore-slicerrc --no-splash \
  --additional-module-paths "$MARKUPS_MODULES" "$MODULE_PROJECT/PneumoconiosisAnnotator" \
  --python-script "$MODULE_PROJECT/scripts/start.py" \
  >> "$MODULE_PROJECT/local-data/slicer.log" 2>&1
