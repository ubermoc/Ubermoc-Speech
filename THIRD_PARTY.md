# Third-party components

These are not covered by the Ubermoc Speech licence; each keeps its own.

## whisper.cpp (`bin/whisper-server`)
MIT License. Copyright (c) 2023-2026 The ggml authors.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions: The above copyright notice and this
permission notice shall be included in all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
Source: https://github.com/ggml-org/whisper.cpp

## parakeet.cpp (`bin/parakeet-server`)
MIT License. Copyright (c) 2026 the parakeet.cpp authors.
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions: The above copyright notice and this
permission notice shall be included in all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
Source: https://github.com/mudler/parakeet.cpp (pinned release in `scripts/fetch-parakeet-server.sh`)

## NVIDIA Parakeet models (downloaded by the user, not shipped in the plugin)
`parakeet-tdt_ctc-110m`, `parakeet-tdt-0.6b-v2` and `parakeet-tdt-0.6b-v3`. Copyright NVIDIA Corporation,
licensed under Creative Commons Attribution 4.0 (CC-BY-4.0): https://creativecommons.org/licenses/by/4.0/
Changes: the models were converted to GGUF and quantised to 8 bits by the parakeet.cpp project; the plugin
downloads those files from https://huggingface.co/mudler/parakeet-cpp-gguf when the user presses Download.
Originals: https://huggingface.co/nvidia/parakeet-tdt_ctc-110m ,
https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2 , https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3

## libgomp (`bin/libgomp.so.1`)
GCC OpenMP runtime, GPL-3.0 with the GCC Runtime Library Exception.
Source: https://gcc.gnu.org/

## Interface libraries
React, @decky/ui, @decky/api and react-icons, under their own licences.

## Silero VAD (`bin/ggml-silero-v6.2.0.bin`)
MIT License. Copyright (c) 2020-present Silero Team. Converted to ggml format by the ggml
authors for whisper.cpp. Used to skip noise and silence before transcription.
Source: https://github.com/snakers4/silero-vad and https://huggingface.co/ggml-org/whisper-vad
