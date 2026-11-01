#!/usr/bin/env bash
# Produce out/"Ubermoc Speech.zip" in Decky's expected layout.
set -euo pipefail
cd "$(dirname "$0")/.."
pnpm install --frozen-lockfile
pnpm build
rm -rf out && mkdir -p "out/Ubermoc Speech"
git rev-parse --short=7 HEAD > "out/Ubermoc Speech/BUILD"
cp -r dist bin py_modules main.py plugin.json package.json LICENSE VERSION "out/Ubermoc Speech/"
find out -name __pycache__ -type d -prune -exec rm -rf {} +
(cd out && zip -qr "Ubermoc Speech.zip" "Ubermoc Speech")
ls -la "out/Ubermoc Speech.zip"
