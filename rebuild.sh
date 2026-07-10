#!/usr/bin/env bash
# rebuild the rare-earth game executable from the current source.
#
#   ./rebuild.sh
#
# wipes the previous build/ and dist/ so nothing stale is reused, then runs
# PyInstaller against the tracked spec. output lands in dist/rare-earth/.
set -euo pipefail

cd "$(dirname "$0")"

echo "[rebuild] cleaning build/ and dist/ ..."
rm -rf build dist

echo "[rebuild] running PyInstaller ..."
python -m PyInstaller rare-earth.spec --noconfirm

echo
echo "[rebuild] done -> dist/rare-earth/rare-earth.exe"
echo "[rebuild] run it with:  ./dist/rare-earth/rare-earth.exe"
