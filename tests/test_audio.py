# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Tests for py_modules/ubermoc_audio.py with simulated audio (no microphone). Run: python3 -m unittest discover -s tests -v"""
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
import ubermoc_audio as A  # noqa: E402


def chunk(amp):
    """100 ms of a 200 Hz tone with the given amplitude (0 = faint noise)."""
    s = [int(amp * math.sin(2 * math.pi * 200 * i / 16000)) + (3 if i % 7 == 0 else -2) for i in range(1600)]
    return (sum(x * x for x in s) / 1600) ** 0.5, max(abs(x) for x in s), struct.pack("<1600h", *s)


def fake(parts):
    """parts: [(seconds, amplitude)], then silence for ever (as a live microphone would give)."""
    def gen():
        for sec, amp in parts:
            for _ in range(int(sec * 10)):
                yield chunk(amp)
        for _ in range(400):
            yield chunk(0)
    return gen


class Record(unittest.TestCase):
    def run_case(self, parts, **kw):
        path = tempfile.mktemp(suffix=".wav")
        try:
            reason, sec = A.record_auto(path, fake(parts), **kw)
            _s, peak = A.wav_stats(path)
        finally:
            if os.path.exists(path):
                os.remove(path)
        return reason, sec, peak

    def test_stops_by_itself_after_speech(self):
        reason, sec, peak = self.run_case([(0.5, 0), (4, 6000)])
        self.assertEqual(reason, "finished")
        self.assertTrue(5.5 < sec < 6.8, sec)
        self.assertGreater(peak, 5000)

    def test_speech_that_starts_at_once_is_not_mistaken_for_noise(self):
        reason, sec, _ = self.run_case([(3, 6000)])
        self.assertEqual(reason, "finished")
        self.assertTrue(4.0 < sec < 5.2, sec)

    def test_a_short_pause_inside_a_sentence_does_not_cut_it(self):
        reason, sec, _ = self.run_case([(1, 6000), (0.8, 0), (2, 6000)])
        self.assertEqual(reason, "finished")
        self.assertGreater(sec, 4.5)

    def test_a_dead_microphone_is_reported(self):
        reason, sec, _ = self.run_case([(15, 0)])
        self.assertEqual(reason, "nothing-heard")
        self.assertTrue(10.0 <= sec <= 10.3, sec)

    def test_a_quiet_voice_is_still_recorded(self):
        self.assertEqual(self.run_case([(0.5, 0), (3, 1200)])[0], "finished")

    def test_endless_talking_is_cut_off(self):
        self.assertEqual(self.run_case([(30, 6000)])[0], "too-long")

    def test_cancel_stops_at_once_and_levels_are_reported(self):
        seen = []
        reason, sec, _ = self.run_case([(10, 6000)], on_level=lambda r, speaking: seen.append(speaking), cancel=lambda: len(seen) >= 12)
        self.assertEqual(reason, "cancelled")
        self.assertLess(sec, 1.5)
        self.assertTrue(any(seen))


class Level(unittest.TestCase):
    def test_level_check_and_verdicts(self):
        peak, level = A.level_check(fake([(1, 8000)]), seconds=1.0)
        self.assertGreater(peak, 7000)
        self.assertGreater(level, 4000)
        self.assertEqual(A.mic_verdict(100)[0], "nothing")
        self.assertEqual(A.mic_verdict(2000)[0], "quiet")
        self.assertEqual(A.mic_verdict(9000)[0], "good")
        self.assertEqual(A.mic_verdict(32768)[0], "loud")
        self.assertLess(A.db(1), -80)
        self.assertGreater(A.db(16384), -7)


if __name__ == "__main__":
    unittest.main()
