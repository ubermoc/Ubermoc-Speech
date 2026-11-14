# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""The speech models and the two speech engines behind them. Pure data and small functions, so it can be tested
without Decky. main.py asks this module which program to start, which file to load and what to send it.

Two engines, one shape: a small local HTTP server that takes a WAV and answers with text.
  whisper  - bin/whisper-server (whisper.cpp). Models are ggml-<name>.bin files. Has the Silero voice detector.
  parakeet - bin/parakeet-server (parakeet.cpp, NVIDIA Parakeet models in GGUF). Models are parakeet-<file>.gguf.
             The server has no voice detector option; a transducer model returns empty text for silence on its own.
"""
import re

WHISPER_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
# Models that are not in the whisper.cpp repo. The file is still saved as ggml-<name>.bin.
MODEL_URLS = {
    "distil-large-v3": "https://huggingface.co/distil-whisper/distil-large-v3-ggml/resolve/main/ggml-distil-large-v3.bin",
    "distil-large-v3.5": "https://huggingface.co/distil-whisper/distil-large-v3.5-ggml/resolve/main/ggml-model.bin",
}
# name -> (approx size in MB, description). The file is ggml-<name>.bin.
# Every description follows one pattern: languages, accuracy, speed, memory. Where the numbers come from:
#   memory  - whisper.cpp README "Memory usage" table (tiny ~0.3 GB, base ~0.4, small ~0.9, medium ~2.1, large ~3.9); "est." = scaled from it
#             for compressed (q5 / q8) and distilled models, which the table does not list.
#   speed   - OpenAI Whisper README, speed relative to the large model (tiny ~10x, base ~7x, small ~4x, medium ~2x, turbo ~8x);
#             distil-large-v3 "6.3x faster than large-v3" is from its model card.
#   accuracy- a relative tier (basic < fair < good < very good < near-best < best), typical for the model family,
#             NOT measured on your microphone. Distil-Whisper figures are from its model cards.
# Nothing here was measured on a Steam Deck or on your hardware: a slower processor makes every model slower.
_BASE_MODELS = {
    # ---- English only (a little more accurate for English at the same size; smaller models gain most)
    "tiny.en": (75, "English only. Basic accuracy. Fastest (about 10x the large model). Memory about 0.3 GB. Fine for short, clear commands."),
    "base.en": (142, "English only. Fair accuracy. Very fast (about 7x large). Memory about 0.4 GB. Good for short phrases."),
    "base.en-q5_1": (57, "English only. Compressed base: smaller download and less memory, accuracy close to base.en."),
    "small.en": (466, "English only. Good accuracy. Fast (about 4x large). Memory about 0.9 GB. A good balance on most computers."),
    "small.en-q5_1": (181, "English only. Compressed small: about 60% smaller, accuracy close to small.en. Memory about 0.5 GB (est.)."),
    "medium.en": (1533, "English only. Very good accuracy. Moderate speed (about 2x large). Memory about 2.1 GB. Slow on weak processors."),
    "medium.en-q5_0": (514, "English only. Compressed medium: a third of the size, accuracy close to medium.en. Memory about 0.8 GB (est.)."),
    "distil-large-v3": (1449, "English only. Near-large accuracy (within about 1% of large-v3's error on long audio), about 6x faster. Memory about 2 GB (est.)."),
    "distil-large-v3.5": (1449, "English only. Newest distilled model: near-large accuracy, a little lower error than turbo on short clips (per its card). Memory about 2 GB (est.)."),
    # ---- Many languages (about 99)
    "tiny": (75, "Many languages. Basic accuracy. Fastest (about 10x large). Memory about 0.3 GB."),
    "base": (142, "Many languages. Fair accuracy. Very fast (about 7x large). Memory about 0.4 GB."),
    "small": (466, "Many languages. Good accuracy. Fast (about 4x large). Memory about 0.9 GB."),
    "small-q5_1": (181, "Many languages. Compressed small: about 60% smaller, accuracy close to small. Memory about 0.5 GB (est.)."),
    "medium": (1533, "Many languages. Very good accuracy. Moderate speed (about 2x large). Memory about 2.1 GB."),
    "medium-q5_0": (514, "Many languages. Compressed medium: a third of the size, accuracy close to medium. Memory about 0.8 GB (est.)."),
    "large-v3-turbo-q5_0": (547, "Many languages. Near-best accuracy, heavily compressed turbo (about 8x large). Memory about 1 GB (est.). May lose a little accuracy against the original."),
    "large-v3-turbo-q8_0": (834, "Many languages. Near-best accuracy, lightly compressed turbo (about 8x large): bigger and closer to the original than q5_0. Memory about 1.3 GB (est.)."),
    "large-v3-turbo": (1624, "Many languages. Near-best accuracy, the original turbo (about 8x large). Memory about 2 GB (est.)."),
    "large-v3-q5_0": (1031, "Many languages. Best accuracy, compressed. Slower than turbo (large speed). Memory about 2 GB (est.)."),
    "large-v3": (2952, "Many languages. Best accuracy, the original. The slowest and heaviest. Memory about 3.9 GB."),
    # ---- Parakeet (NVIDIA models, parakeet.cpp engine). No voice detector switch: they return nothing for silence.
    "parakeet-110m": (170, "English only. Parakeet (NVIDIA), small: good accuracy, very fast. Memory about 0.3 GB (est.). The voice detector is not used."),
    "parakeet-tdt-0.6b-v2": (862, "English only. Parakeet (NVIDIA): near-best accuracy; rated above Whisper large-v3 for English in published tests. Memory about 1 GB (est.). No voice detector."),
    "parakeet-tdt-0.6b-v3": (897, "25 European languages. Parakeet (NVIDIA): near-best accuracy for them. Memory about 1 GB (est.). No voice detector."),
}

# What we measured. A comparison of ten English models on ONE speaker's recordings with ONE noisy microphone:
# 52 clips (normal sentences, spelled letters and digits, numbers like fifteen/fifty, sound-alike words such as two/to/too),
# 16 of them with background noise. Values: % wrong for (sentences quiet, sentences noisy, spelled quiet, spelled noisy, numbers, sound-alikes).
# A different voice, microphone or room can change the order; one clip moves a figure by several points.
MEASURED = {
    "parakeet-tdt-0.6b-v2": (4.3, 6.4, 0.0, 4.2, 10.0, 0.0),
    "small.en": (5.5, 11.5, 0.0, 4.2, 10.0, 0.0),
    "medium.en": (9.8, 9.0, 0.0, 4.2, 10.0, 0.0),
    "large-v3-turbo": (5.5, 9.0, 8.2, 4.2, 5.6, 3.2),
    "large-v3-turbo-q8_0": (5.5, 9.0, 8.2, 4.2, 5.6, 3.2),
    "large-v3": (5.5, 7.7, 8.2, 16.7, 0.0, 0.0),
    "parakeet-110m": (8.6, 7.7, 14.8, 20.8, 2.8, 3.2),
    "base.en": (5.5, 7.7, 13.1, 20.8, 10.0, 12.9),
    "tiny.en": (7.4, 10.3, 3.3, 25.0, 66.7, 6.5),
    "distil-large-v3.5": (6.7, 7.7, 44.3, 70.8, 35.0, 3.2),
}
# The panel shows the recommended models first.
RECOMMENDED = {
    "parakeet-tdt-0.6b-v2": "RECOMMENDED. Fewest mistakes in our test, also with background noise.",
    "small.en": "LIGHTER OPTION. Almost as accurate in quiet, very fast, smallest download of the accurate models.",
}


def _measured(name):
    m = MEASURED.get(name)
    if not m:
        return " Not part of our voice test."
    sq, sn, pq, pn, nu, ho = (f"{v:.0f}" if v >= 10 or v == int(v) else f"{v:.1f}" for v in m)
    return (f" Our voice test (one English speaker, noisy microphone), share wrong: sentences {sq}% quiet / {sn}% noisy, "
            f"spelled letters and digits {pq}% / {pn}%, numbers like fifteen/fifty {nu}%, sound-alike words {ho}%.")


def _describe(name, base):
    if name in RECOMMENDED:
        base = base.replace("English only. ", "English only. " + RECOMMENDED[name] + " ", 1)
    return base + _measured(name)


_ORDER = list(RECOMMENDED) + [n for n in _BASE_MODELS if n not in RECOMMENDED]
MODELS = {n: (_BASE_MODELS[n][0], _describe(n, _BASE_MODELS[n][1])) for n in _ORDER}
DEFAULT_MODEL = "base.en"


def visible_models(is_installed):
    """What the panel lists: every model, with whether it is on this computer."""
    return [{"name": n, "size_mb": mb, "description": d, "installed": bool(is_installed(n))} for n, (mb, d) in MODELS.items()]


def choose_model(saved, is_installed):
    """The model to use: the saved choice if it is known and installed; otherwise the first listed model that is installed
    (the recommended ones come first); otherwise none."""
    if saved in MODELS and is_installed(saved):
        return saved
    for n in MODELS:
        if is_installed(n):
            return n
    return ""


# Parakeet model name -> file in the GGUF repo (8-bit versions). Saved as parakeet-<file>.
PARAKEET_BASE_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/main"
PARAKEET_FILES = {
    "parakeet-110m": "tdt_ctc-110m-q8_0.gguf",
    "parakeet-tdt-0.6b-v2": "tdt-0.6b-v2-q8_0.gguf",
    "parakeet-tdt-0.6b-v3": "tdt-0.6b-v3-q8_0.gguf",
}
ENGINE_BINARY = {"whisper": "whisper-server", "parakeet": "parakeet-server"}



def engine_of(name: str) -> str:
    return "parakeet" if name in PARAKEET_FILES else "whisper"


def binary_name(name: str) -> str:
    """The server program a model needs."""
    return ENGINE_BINARY[engine_of(name)]


def model_filename(name: str) -> str:
    if name in PARAKEET_FILES:
        return f"parakeet-{PARAKEET_FILES[name]}"
    return f"ggml-{name}.bin"


def model_url(name: str) -> str:
    if name in PARAKEET_FILES:
        return f"{PARAKEET_BASE_URL}/{PARAKEET_FILES[name]}"
    return MODEL_URLS.get(name, f"{WHISPER_BASE_URL}/ggml-{name}.bin")


def vad_supported(name: str) -> bool:
    """Only the Whisper engine has the voice detector switch."""
    return engine_of(name) == "whisper"


def server_command(name: str, binary: str, model_path: str, host: str, port: int, threads: int,
                   vad_model: str = "", vad_on: bool = False, vad_threshold: str = "0.08") -> list:
    """The command line that starts the server for this model."""
    if engine_of(name) == "parakeet":
        return [binary, "--model", model_path, "--host", host, "--port", str(port), "--threads", str(threads)]
    cmd = [binary, "-m", model_path, "--host", host, "--port", str(port), "-t", str(threads), "-nt"]
    if vad_model and vad_on:
        # Only audio the voice detector hears as speech reaches the model, so noise and silence
        # are not turned into stray words. The padding and low minimum keep the edges of short utterances.
        cmd += ["--vad", "-vm", vad_model, "-vp", "300", "-vspd", "120", "-vt", vad_threshold]
    return cmd


def transcribe_request(name: str, seconds: float, short_audio_context: bool = True):
    """(url path, form fields) for one WAV of `seconds` length."""
    if engine_of(name) == "parakeet":
        return "/v1/audio/transcriptions", {"response_format": "json"}
    fields = {"response_format": "json", "temperature": "0.0", "language": "en"}
    if short_audio_context:
        # Whisper normally encodes a full 30 s window even for a 3 s clip; telling it the real
        # length makes short recordings much faster. 50 encoder frames per second, plus 2 s of margin.
        fields["audio_ctx"] = str(min(1500, max(500, int((seconds + 2) * 50))))
    return "/inference", fields
