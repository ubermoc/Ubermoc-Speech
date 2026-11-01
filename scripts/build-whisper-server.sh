#!/usr/bin/env bash
# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
# Rebuild bin/whisper-server (Vulkan, static ggml). Run on Arch/SteamOS-like
# glibc or in an archlinux container; needs: cmake gcc vulkan-headers
# shaderc (glslc) spirv-headers. SteamOS: `sudo steamos-readonly disable` first.
set -euo pipefail
cd "$(dirname "$0")/.."
WORK="$(mktemp -d)"
git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WORK/src"
cmake -S "$WORK/src" -B "$WORK/b" -DGGML_VULKAN=1 -DBUILD_SHARED_LIBS=OFF \
  -DWHISPER_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build "$WORK/b" -j"$(nproc)" --target whisper-server
install -D -m755 "$WORK/b/bin/whisper-server" bin/whisper-server
strip bin/whisper-server
cp -L "$(ldconfig -p | awk '/libgomp.so.1/{print $NF; exit}')" bin/libgomp.so.1
echo "built bin/whisper-server"
