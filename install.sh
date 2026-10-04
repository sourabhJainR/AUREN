#!/usr/bin/env bash
set -euo pipefail

REPO="sourabhJainR/AUREN"
VERSION="${AUREN_VERSION:-latest}"
BASE="https://github.com/${REPO}/releases/${VERSION}/download"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL "${BASE}/auren-portable.zip" -o "$TMP/auren-portable.zip"
unzip -p "$TMP/auren-portable.zip" auren_cli.py > "$TMP/auren_cli.py"
python3 "$TMP/auren_cli.py" "$TMP/auren-portable.zip"
