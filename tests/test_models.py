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

OLDER = {"tiny.en", "base.en", "base.en-q5_1", "small.en-q5_1", "medium.en", "medium.en-q5_0", "distil-large-v3", "distil-large-v3.5",
         "tiny", "base", "small", "small-q5_1", "medium", "medium-q5_0", "large-v3-turbo-q5_0", "large-v3-turbo-q8_0", "large-v3-turbo",
         "large-v3-q5_0", "large-v3", "parakeet-110m", "parakeet-tdt-0.6b-v3"}


class ModelTable(unittest.TestCase):
    def test_only_the_two_recommended_models_are_offered(self):
        self.assertEqual(list(M.MODELS), ["parakeet-tdt-0.6b-v2", "small.en"])
        self.assertEqual(M.DEFAULT_MODEL, "parakeet-tdt-0.6b-v2")

    def test_offered_descriptions_say_who_measured_what(self):
        for name, (mb, d) in M.MODELS.items():
            self.assertTrue(d.startswith("English only."), name)
            self.assertIn("one English speaker", d, name + ": must say the numbers come from one speaker")
            self.assertIn("%", d, name)
            self.assertTrue(30 <= mb <= 4000, f"{name}: {mb} MB")
        self.assertIn("RECOMMENDED", M.MODELS["parakeet-tdt-0.6b-v2"][1])
        self.assertIn("voice detector", M.MODELS["parakeet-tdt-0.6b-v2"][1].lower())

    def test_every_earlier_model_is_still_known(self):
        self.assertEqual(set(M.LEGACY_MODELS), OLDER)
        self.assertFalse(set(M.LEGACY_MODELS) & set(M.MODELS))
        self.assertEqual(set(M.KNOWN), OLDER | set(M.MODELS))
        for name, (mb, d) in M.LEGACY_MODELS.items():
            self.assertTrue(d.startswith("Older option, no longer offered for download."), name)

    def test_names_are_plain_and_files_are_unique(self):
        for name in M.KNOWN:
            self.assertRegex(name, r"^[a-z0-9][a-z0-9._-]*$", name)
        files = [M.model_filename(n) for n in M.KNOWN]
        self.assertEqual(len(files), len(set(files)), "two models would share one file")

    def test_the_panel_lists_offered_models_plus_installed_older_ones(self):
        none = M.visible_models(lambda n: False)
        self.assertEqual([x["name"] for x in none], ["parakeet-tdt-0.6b-v2", "small.en"])
        self.assertFalse(any(x["installed"] for x in none))
        have = {"small.en", "medium.en", "large-v3-turbo"}
        shown = M.visible_models(have.__contains__)
        self.assertEqual([x["name"] for x in shown], ["parakeet-tdt-0.6b-v2", "small.en", "medium.en", "large-v3-turbo"])
        self.assertEqual({x["name"]: x["installed"] for x in shown}, {"parakeet-tdt-0.6b-v2": False, "small.en": True, "medium.en": True, "large-v3-turbo": True})

    def test_model_choice_never_strands_an_existing_user(self):
        have = {"medium.en", "small.en"}.__contains__
        self.assertEqual(M.choose_model("medium.en", have), "medium.en")          # an older model that is installed keeps working
        self.assertEqual(M.choose_model("large-v3", have), "small.en")           # a choice that is gone falls back to an offered model that is installed
        self.assertEqual(M.choose_model(None, have), "small.en")
        self.assertEqual(M.choose_model("base.en", {"parakeet-tdt-0.6b-v2"}.__contains__), "parakeet-tdt-0.6b-v2")
        self.assertEqual(M.choose_model("medium.en", {"medium.en"}.__contains__), "medium.en")
        self.assertEqual(M.choose_model("base.en", set().__contains__), "")
        self.assertEqual(M.choose_model("not-a-model", {"not-a-model"}.__contains__), "")


class Engines(unittest.TestCase):
    def test_parakeet_files_belong_to_known_models(self):
        self.assertEqual({n for n in M.KNOWN if n.startswith("parakeet")}, set(M.PARAKEET_FILES))

    def test_engine_choice(self):
        self.assertEqual(M.engine_of("parakeet-110m"), "parakeet")
        self.assertEqual(M.engine_of("large-v3-turbo"), "whisper")
        self.assertEqual(M.binary_name("parakeet-tdt-0.6b-v2"), "parakeet-server")
        self.assertEqual(M.binary_name("base.en"), "whisper-server")

    def test_files_and_urls(self):
        self.assertEqual(M.model_filename("base.en"), "ggml-base.en.bin")
        self.assertEqual(M.model_filename("parakeet-110m"), "parakeet-tdt_ctc-110m-q8_0.gguf")
        self.assertTrue(M.model_url("parakeet-110m").endswith("/mudler/parakeet-cpp-gguf/resolve/main/tdt_ctc-110m-q8_0.gguf"))
        self.assertTrue(M.model_url("parakeet-tdt-0.6b-v2").endswith("/mudler/parakeet-cpp-gguf/resolve/main/tdt-0.6b-v2-q8_0.gguf"))
        self.assertTrue(M.model_url("small.en").endswith("/ggml-small.en.bin"))
        self.assertIn("distil-large-v3.5-ggml", M.model_url("distil-large-v3.5"))

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
