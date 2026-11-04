#!/bin/bash
# Builds both macOS apps:
#   dist/ProfessorCatLife.app   (comes with the default playlist)
#   dist/ProfessorCatStudy.app  (no default playlist; right-click > Add your study playlist)
#
# Run on a Mac from this folder:   bash build_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install --upgrade "pyside6>=6.6" "pyinstaller>=6.3"

if [ ! -d cat_sprites_life ]; then
  echo "cat_sprites_life/ is missing - copy it from the Windows folder (or run prepare_life_sprites.py)." >&2
  exit 1
fi

for EDITION in Life Study; do
  LOWER=$(echo "$EDITION" | tr '[:upper:]' '[:lower:]')
  "$PY" -m PyInstaller --noconfirm --clean --windowed \
    --name "ProfessorCat${EDITION}" \
    --osx-bundle-identifier "com.professorcat.${LOWER}" \
    --add-data "cat_sprites_life:cat_sprites_life" \
    --hidden-import PySide6.QtWebEngineWidgets \
    --hidden-import PySide6.QtWebEngineCore \
    --exclude-module tkinter --exclude-module numpy --exclude-module scipy \
    --exclude-module PIL --exclude-module rembg --exclude-module onnxruntime \
    "ProfessorCat${EDITION}.py"
done

echo
echo "Done:"
ls -d dist/*.app
echo
echo "First launch: right-click the app > Open (the apps are not signed by Apple)."
