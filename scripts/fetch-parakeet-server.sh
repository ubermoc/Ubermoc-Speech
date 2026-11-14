#!/usr/bin/env bash
# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
# Install bin/parakeet-server from the pinned parakeet.cpp release (Vulkan, Linux x64, MIT licence).
# The download is checked against a recorded SHA-256, so a changed or damaged file is refused.
# To move to a newer release: change TAG, run this once with SHA256=skip, read the printed value, put it
# in SHA256 below, run the tests, and test the new server with a real model before releasing.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="v0.6.1"
SHA256="881fd99d531a4dcfc26119a4969aec61b8390ba84e9d641716411d4c1db2de3a"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
URL="https://github.com/mudler/parakeet.cpp/releases/download/$TAG/parakeet-$TAG-bin-linux-vulkan-x64.tar.gz"
curl -fsSL --retry 3 -o "$WORK/p.tar.gz" "$URL"
GOT="$(sha256sum "$WORK/p.tar.gz" | cut -d' ' -f1)"
if [ "$SHA256" != "skip" ] && [ "$GOT" != "$SHA256" ]; then
  echo "REFUSED: checksum mismatch. expected $SHA256 got $GOT" >&2; exit 1
fi
tar xzf "$WORK/p.tar.gz" -C "$WORK"
install -D -m755 "$WORK"/parakeet-*/parakeet-server bin/parakeet-server
echo "installed bin/parakeet-server ($TAG, sha256 $GOT)"
