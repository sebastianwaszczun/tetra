#!/bin/sh
# Buduje docs/*.pdf ze źródeł HTML w tym folderze (Chrome/Chromium ≥ 131 – numery stron z @page).
#   CHROME=/ścieżka/do/chrome sh docs/src/build.sh [NAZWA ...]
# Schematy (img/*.png) odtwarza: python docs/src/diagrams.py (Pillow).
# Fonty (Inter, Inter Tight, Caveat – SIL OFL 1.1) pobierane z github.com/google/fonts przy pierwszym uruchomieniu.
set -e
cd "$(dirname "$0")"
CHROME="${CHROME:-chromium}"
GF=https://github.com/google/fonts/raw/main/ofl
mkdir -p fonts
[ -f fonts/Inter.ttf ]      || curl -sSLo fonts/Inter.ttf      "$GF/inter/Inter%5Bopsz,wght%5D.ttf"
[ -f fonts/InterTight.ttf ] || curl -sSLo fonts/InterTight.ttf "$GF/intertight/InterTight%5Bwght%5D.ttf"
[ -f fonts/Caveat.ttf ]     || curl -sSLo fonts/Caveat.ttf     "$GF/caveat/Caveat%5Bwght%5D.ttf"
for name in ${@:-METODYKA INSTALACJA KAMPANIE LICENCJE}; do
  "$CHROME" --headless --no-sandbox --disable-gpu --no-pdf-header-footer --virtual-time-budget=5000 \
    --print-to-pdf="../$name.pdf" "file://$PWD/$name.html" 2>/dev/null
  echo "docs/$name.pdf"
done
