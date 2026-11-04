# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Tests for py_modules/ubermoc_rumble.py. Run: python3 -m unittest discover -s tests -v"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
import ubermoc_rumble  # noqa: E402


SAMPLE = """I: Bus=0003 Vendor=045e Product=028e Version=0110
N: Name="Xbox pad"
H: Handlers=event5 js0
B: EV=20000b
B: KEY=7cdb000000000000 0 0 0 0

I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Keyboard"
H: Handlers=sysrq kbd event3
B: EV=120013
B: KEY=1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe

I: Bus=0003 Vendor=054c Product=09cc Version=0111
N: Name="Pad without rumble"
H: Handlers=event7 js1
B: EV=1b
B: KEY=7cdb000000000000 0 0 0 0
"""


class Pads(unittest.TestCase):
    def test_only_rumbling_gamepads(self):
        self.assertEqual(ubermoc_rumble.ff_pads(SAMPLE), ["event5"])

    def test_effect_layout(self):
        e = ubermoc_rumble.effect_bytes(0x1234, 70)
        self.assertEqual(len(e), ubermoc_rumble.FF_EFFECT_SIZE)
        self.assertEqual(struct.unpack_from("<HhH", e, 0), (ubermoc_rumble.FF_RUMBLE, -1, 0))
        self.assertEqual(struct.unpack_from("<HH", e, 10), (70, 0))       # replay length, delay
        self.assertEqual(struct.unpack_from("<HH", e, 16), (0x1234, 0x1234))

    def test_ioctl_numbers(self):
        if struct.calcsize("P") == 8:
            self.assertEqual(ubermoc_rumble.EVIOCSFF, 0x40304580)
        self.assertEqual(ubermoc_rumble.EVIOCRMFF, 0x40044581)


class Levels(unittest.TestCase):
    def test_ten_steps_each_stronger(self):
        self.assertEqual(sorted(ubermoc_rumble.LEVELS), list(range(1, 11)))
        values = [ubermoc_rumble.LEVELS[n] for n in range(1, 11)]
        self.assertEqual((values[0], values[-1]), (0x0200, 0x6000))
        self.assertEqual(values, sorted(set(values)))

    def test_bad_settings_fall_back(self):
        for bad in (None, 0, 11, "3", 2.5, True, [3]):
            self.assertEqual(ubermoc_rumble.level_of(bad), ubermoc_rumble.DEFAULT_LEVEL)
        self.assertEqual(ubermoc_rumble.level_of(10), 10)

    def test_level_picks_strength(self):
        seen = []
        r = ubermoc_rumble.Rumbler(lambda: ["event5"])
        r._run = lambda which, nodes, strength: seen.append(strength)
        r.play("start", 10)
        r.play("start", 1)
        import time
        time.sleep(0.05)
        self.assertEqual(sorted(seen), [ubermoc_rumble.LEVELS[1], ubermoc_rumble.LEVELS[10]])


class Play(unittest.TestCase):
    def test_nothing_to_buzz(self):
        self.assertFalse(ubermoc_rumble.Rumbler(lambda: []).play("start"))
        self.assertFalse(ubermoc_rumble.Rumbler(lambda: ["event5"]).play("nope"))

    def test_every_buzz_is_stopped(self):
        log = []
        r = ubermoc_rumble.Rumbler(lambda: ["event5"], sleep=lambda s: None)
        r._open = lambda node: 99
        r._start = lambda fd, ms, strength: log.append("on") or 7
        r._erase = lambda fd, effect_id: log.append("off")
        orig = os.close
        os.close = lambda fd: None if fd == 99 else orig(fd)
        try:
            r._run("stop", ["event5"], 0x2000)
        finally:
            os.close = orig
        self.assertEqual(log, ["on", "off", "on", "off"])


if __name__ == "__main__":
    unittest.main()
