#!/usr/bin/env bash
# Packages the assignment exactly as requested:
#
#   <RollNo>-A1-PostIndex-IR-M2026.zip
#     ├── README.txt
#     ├── <RollNo>-src.zip          (src/, tests/, sample_data/, make_submission.sh)
#     └── output/                   (test cases: 10 rare terms + 5 functional tests)
#
# Usage:  ./make_submission.sh <RollNo>
set -euo pipefail
ROLL="${1:?usage: ./make_submission.sh <RollNo>}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cd "$ROOT"
mkdir -p "$STAGE/src-pkg"
cp -r src tests sample_data make_submission.sh README.md "$STAGE/src-pkg/"
find "$STAGE/src-pkg" -name "__pycache__" -type d -prune -exec rm -rf {} +
(cd "$STAGE/src-pkg" && zip -qr "$STAGE/${ROLL}-src.zip" .)

mkdir -p "$STAGE/final"
cp README.md "$STAGE/final/README.txt"
cp "$STAGE/${ROLL}-src.zip" "$STAGE/final/"
cp -r output "$STAGE/final/output"

OUT="$ROOT/${ROLL}-A1-PostIndex-IR-M2026.zip"
rm -f "$OUT"
(cd "$STAGE/final" && zip -qr "$OUT" README.txt "${ROLL}-src.zip" output)
echo "created $OUT"
unzip -l "$OUT"
