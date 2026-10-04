#!/usr/bin/env bash
set -euo pipefail

REPO="sourabhJainR/AUREN"
VERSION="${AUREN_VERSION:-latest}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ "$VERSION" == "latest" ]]; then
  BASE="https://github.com/$REPO/releases/latest/download"
else
  BASE="https://github.com/$REPO/releases/$VERSION/download"
fi

curl -fsSL "$BASE/auren-portable.zip" -o "$TMP/auren-portable.zip"
unzip -p "$TMP/auren-portable.zip" auren_cli.py > "$TMP/auren_cli.py"
python3 "$TMP/auren_cli.py" "$TMP/auren-portable.zip"
