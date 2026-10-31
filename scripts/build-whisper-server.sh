#!/usr/bin/env bash
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
