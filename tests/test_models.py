# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Tests for py_modules/ubermoc_models.py: the model table and the two speech engines. Run: python3 -m unittest discover -s tests -v"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
import ubermoc_models as M  # noqa: E402

ENGLISH_ONLY = {"parakeet-110m", "parakeet-tdt-0.6b-v2"}


def english_only(name):
    return ".en" in name or name.startswith("distil-") or name in ENGLISH_ONLY


class ModelTable(unittest.TestCase):
    def test_names_are_plain_and_unique(self):
        for name in M.MODELS:
            self.assertRegex(name, r"^[a-z0-9][a-z0-9._-]*$", name)

    def test_default_model_exists_and_is_whisper(self):
        self.assertIn(M.DEFAULT_MODEL, M.MODELS)
        self.assertEqual(M.engine_of(M.DEFAULT_MODEL), "whisper")

    def test_sizes_are_real_downloads(self):
        for name, (mb, _d) in M.MODELS.items():
            self.assertTrue(30 <= mb <= 4000, f"{name}: {mb} MB")

    def test_description_says_languages_and_accuracy(self):
        for name, (_mb, d) in M.MODELS.items():
            self.assertTrue(d.startswith(("English only.", "Many languages.", "25 European languages.")), f"{name}: {d[:30]}")
            self.assertIn("accuracy", d.lower(), name)
            self.assertLessEqual(len(d), 175, f"{name}: description too long for the panel")

    def test_english_only_flag_matches_the_name(self):
        for name, (_mb, d) in M.MODELS.items():
            self.assertEqual(d.startswith("English only."), english_only(name), name)

    def test_estimates_are_labelled(self):
        for name, (_mb, d) in M.MODELS.items():
            if re.search(r"q[58]_[01]|distil|turbo|parakeet", name) and "Memory about" in d:
                self.assertIn("(est.)", d, f"{name}: memory is an estimate and must say so")

    def test_extra_whisper_urls_belong_to_listed_models(self):
        for name, url in M.MODEL_URLS.items():
            self.assertIn(name, M.MODELS)
            self.assertTrue(url.startswith("https://huggingface.co/"), name)


class Engines(unittest.TestCase):
    def test_parakeet_models_are_complete(self):
        self.assertEqual({n for n in M.MODELS if n.startswith("parakeet")}, set(M.PARAKEET_FILES))
        for name, d in ((n, M.MODELS[n][1]) for n in M.PARAKEET_FILES):
            self.assertIn("Parakeet (NVIDIA)", d, name)
            self.assertIn("voice detector", d.lower(), name + ": must say the voice detector does not apply")

    def test_engine_choice(self):
        self.assertEqual(M.engine_of("parakeet-110m"), "parakeet")
        self.assertEqual(M.engine_of("large-v3-turbo"), "whisper")
        self.assertEqual(M.binary_name("parakeet-tdt-0.6b-v2"), "parakeet-server")
        self.assertEqual(M.binary_name("base.en"), "whisper-server")

    def test_files_and_urls(self):
        self.assertEqual(M.model_filename("base.en"), "ggml-base.en.bin")
        self.assertEqual(M.model_filename("parakeet-110m"), "parakeet-tdt_ctc-110m-q8_0.gguf")
        self.assertTrue(M.model_url("parakeet-110m").endswith("/mudler/parakeet-cpp-gguf/resolve/main/tdt_ctc-110m-q8_0.gguf"))
        self.assertTrue(M.model_url("small.en").endswith("/ggml-small.en.bin"))
        self.assertIn("distil-large-v3.5-ggml", M.model_url("distil-large-v3.5"))
        names = [M.model_filename(n) for n in M.MODELS]
        self.assertEqual(len(names), len(set(names)), "two models would share one file")

    def test_voice_detector_only_for_whisper(self):
        self.assertTrue(M.vad_supported("base.en"))
        self.assertFalse(M.vad_supported("parakeet-110m"))

    def test_whisper_command(self):
        c = M.server_command("base.en", "/b/whisper-server", "/m/ggml-base.en.bin", "127.0.0.1", 8178, 4)
        self.assertEqual(c[:3], ["/b/whisper-server", "-m", "/m/ggml-base.en.bin"])
        self.assertNotIn("--vad", c)
        v = M.server_command("base.en", "/b/w", "/m/x.bin", "127.0.0.1", 8178, 4, vad_model="/b/silero.bin", vad_on=True, vad_threshold="0.1")
        self.assertIn("--vad", v)
        self.assertEqual(v[v.index("-vt") + 1], "0.1")
        off = M.server_command("base.en", "/b/w", "/m/x.bin", "127.0.0.1", 8178, 4, vad_model="/b/silero.bin", vad_on=False)
        self.assertNotIn("--vad", off)

    def test_parakeet_command_has_no_whisper_options(self):
        c = M.server_command("parakeet-110m", "/b/parakeet-server", "/m/p.gguf", "127.0.0.1", 8178, 4,
                             vad_model="/b/silero.bin", vad_on=True)
        self.assertEqual(c, ["/b/parakeet-server", "--model", "/m/p.gguf", "--host", "127.0.0.1", "--port", "8178", "--threads", "4"])

    def test_requests(self):
        path, fields = M.transcribe_request("parakeet-110m", 3.0)
        self.assertEqual(path, "/v1/audio/transcriptions")
        self.assertEqual(fields, {"response_format": "json"})
        path, fields = M.transcribe_request("base.en", 3.0)
        self.assertEqual(path, "/inference")
        self.assertEqual(fields["language"], "en")
        self.assertEqual(fields["audio_ctx"], "500")                      # short clip: never below 500
        self.assertEqual(M.transcribe_request("base.en", 100.0)[1]["audio_ctx"], "1500")   # long clip: never above 1500
        self.assertNotIn("audio_ctx", M.transcribe_request("base.en", 3.0, short_audio_context=False)[1])


if __name__ == "__main__":
    unittest.main()
