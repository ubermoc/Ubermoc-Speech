#!/usr/bin/env bash
# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
# Produce out/"Ubermoc Speech.zip" in Decky's expected layout.
set -euo pipefail
cd "$(dirname "$0")/.."
pnpm install --frozen-lockfile
pnpm build
rm -rf out && mkdir -p "out/Ubermoc Speech"
git rev-parse --short=7 HEAD > "out/Ubermoc Speech/BUILD"
cp -r dist bin py_modules main.py plugin.json package.json LICENSE THIRD_PARTY.md VERSION "out/Ubermoc Speech/"
find out -name __pycache__ -type d -prune -exec rm -rf {} +
(cd out && zip -qr "Ubermoc Speech.zip" "Ubermoc Speech")
ls -la "out/Ubermoc Speech.zip"
