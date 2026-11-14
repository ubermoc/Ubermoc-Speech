# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Tests for py_modules/ubermoc_voicetest.py: scoring, the verdict and the disk-space check. Run: python3 -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
import ubermoc_voicetest as V  # noqa: E402


def sp(ref, hyp):
    return V.score_phrase(("spelled", "", [ref], ""), hyp)[1]


def nu(accept, hyp):
    return V.score_phrase(("numbers", "", accept, ""), hyp)[1]


class Phrases(unittest.TestCase):
    def test_the_set_is_short_and_covers_every_kind(self):
        self.assertEqual(len(V.PHRASES), 22)
        self.assertEqual({p[0] for p in V.PHRASES}, {"sentence", "spelled", "numbers", "homophone"})
        for kind, text, accept, hint in V.PHRASES:
            self.assertTrue(text.strip())
            if kind in ("spelled", "numbers"):
                self.assertTrue(accept, text)
            if kind == "spelled":
                self.assertIn("say each one", hint)

    def test_letter_hints_name_the_letters(self):
        self.assertIn("bee, dee, pee, tee", V.PHRASES[10][3])
        self.assertIn("five, nine, five, nine, four", V.PHRASES[14][3])


class Scoring(unittest.TestCase):
    def test_letters_in_every_written_form(self):
        for hyp in ("B, D, P, T.", "BDPT", "bee dee pee tee", "B-D-P-T"):
            self.assertEqual(sp("BDPT", hyp), 0, hyp)

    def test_letter_mistakes_are_counted(self):
        self.assertEqual(sp("BDPT", "B, E, P, T"), 1)                # a swap
        self.assertEqual(sp("BDPT", "B, D, T"), 1)                   # a drop
        self.assertEqual(sp("BDPT", "B, D, P, T, thanks"), 1)        # an extra word
        self.assertEqual(V.score_phrase(("spelled", "", ["BDPT"], ""), "B, E, P, T")[3], [("d", "e")])

    def test_digits(self):
        for hyp in ("5 9 5 9 4", "five nine five nine four", "59594"):
            self.assertEqual(sp("59594", hyp), 0, hyp)
        self.assertEqual(sp("59594", "5, 5, 5, 9, 4"), 1)
        self.assertEqual(sp("55501234", "five five five oh one two three four"), 0)
        self.assertEqual(sp("B4D8P2", "B4 D8 P2"), 0)
        self.assertEqual(sp("B4D8P2", "B 4 T 8 P 2"), 1)

    def test_teen_and_ten_numbers(self):
        a = ["13 30 14 40", "thirteen thirty fourteen forty"]
        self.assertEqual(nu(a, "13, 30, 14, 40"), 0)
        self.assertEqual(nu(a, "Thirteen, thirty, fourteen, forty."), 0)
        self.assertEqual(nu(["15 50 16 60", "fifteen fifty sixteen sixty"], "15, 15, 16, 60"), 1)
        y = ["1999 2024", "nineteen ninety nine twenty twenty four"]
        self.assertEqual(nu(y, "1999, 2024"), 0)
        self.assertEqual(nu(y, "Nineteen ninety-nine, twenty twenty-four."), 0)

    def test_sentences_and_sound_alikes(self):
        h = ("homophone", "She ate eight cookies.", [], "")
        self.assertEqual(V.score_phrase(h, "She ate 8 cookies.")[1], 0)
        self.assertEqual(V.score_phrase(h, "She eight 8 cookies.")[1], 1)
        s = ("sentence", "Meet me outside Orgrimmar at the bank.", [], "")
        self.assertEqual(V.score_phrase(s, "Meet me outside Orgrimmar at the bank.")[1], 0)
        self.assertEqual(V.score_phrase(s, "Meet me outside of Ogmar at the bank.")[1], 2)

    def test_one_runaway_transcript_counts_at_most_100_percent(self):
        s = ("sentence", "She ate eight cookies.", [], "")
        g, e, n, _ = V.score_phrase(s, "blah " * 200)
        self.assertEqual((g, e, n), ("sentence", n, n))

    def test_summary_averages_the_groups(self):
        rows = [{"group": "sentence", "errors": 1, "units": 10, "sec": 0.2}, {"group": "sentence", "errors": 0, "units": 10, "sec": 0.4},
                {"group": "spelled", "errors": 2, "units": 10, "sec": 0.3}]
        s = V.summarize(rows)
        self.assertEqual(s["groups"], {"sentence": 5.0, "spelled": 20.0})
        self.assertEqual(s["score"], 12.5)
        self.assertEqual(s["median_s"], 0.3)
        self.assertIsNone(V.summarize([])["score"])


class Verdict(unittest.TestCase):
    def r(self, score, sec=0.2, mem=1000):
        return {"score": score, "median_s": sec, "memory_mb": mem, "groups": {}}

    def test_a_clear_winner(self):
        v = V.verdict({"a": self.r(4.0), "b": self.r(8.0), "c": self.r(12.0)})
        self.assertEqual((v["best"], v["too_close"], v["ranking"]), ("a", False, ["a", "b", "c"]))
        self.assertIn("fewest mistakes", v["reason"])
        self.assertIn("8% for b", v["reason"])

    def test_a_tie_goes_to_the_faster_model_and_says_so(self):
        v = V.verdict({"slow": self.r(4.0, sec=0.9), "fast": self.r(4.8, sec=0.1), "bad": self.r(15.0)})
        self.assertEqual((v["best"], v["too_close"], set(v["close"])), ("fast", True, {"slow", "fast"}))
        self.assertIn("too close to call", v["reason"])

    def test_everything_bad_says_to_check_the_setup(self):
        self.assertIn("check the microphone", V.verdict({"a": self.r(40.0), "b": self.r(50.0)})["reason"])

    def test_no_results(self):
        v = V.verdict({"a": {"score": None}})
        self.assertIsNone(v["best"])
        self.assertEqual(V.verdict({})["ranking"], [])


class Space(unittest.TestCase):
    def test_nothing_to_download(self):
        p = V.space_plan({"a": 800}, {"a"}, 50000)
        self.assertEqual((p["level"], p["download_mb"]), ("ok", 0))

    def test_plenty_of_room(self):
        self.assertEqual(V.space_plan({"a": 800, "b": 400}, set(), 500000)["level"], "ok")

    def test_a_large_share_of_free_space_warns(self):
        p = V.space_plan({"a": 3000}, set(), 5500)
        self.assertEqual(p["level"], "warn")
        self.assertIn("delete the models you do not keep", p["message"])

    def test_not_enough_space_stops(self):
        p = V.space_plan({"a": 3000}, set(), 4000)
        self.assertEqual(p["level"], "stop")
        self.assertIn("Not enough space", p["message"])

    def test_a_handheld_is_stricter(self):
        sel = {"a": 3500}
        self.assertEqual(V.space_plan(sel, set(), 12000, handheld=False)["level"], "ok")
        w = V.space_plan(sel, set(), 12000, handheld=True)
        self.assertEqual(w["level"], "warn")
        self.assertTrue(w["message"].startswith("On a handheld"))

    def test_device_and_disk_helpers(self):
        self.assertTrue(V.is_handheld("Jupiter"))
        self.assertTrue(V.is_handheld("Galileo"))
        self.assertFalse(V.is_handheld("MS-7E12"))
        self.assertGreater(V.free_mb(tempfile.gettempdir()), 0)
        self.assertGreater(V.free_mb("/definitely/not/a/real/folder"), 0)     # climbs to the nearest folder that exists
        self.assertGreaterEqual(V.process_memory_mb(os.getpid()), 1)


if __name__ == "__main__":
    unittest.main()
