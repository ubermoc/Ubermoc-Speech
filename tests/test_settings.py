# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Tests for py_modules/ubermoc_settings.py. Run: python3 -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
from ubermoc_settings import SettingsStore  # noqa: E402


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = os.path.join(self.tmp.name, "settings", "settings.json")
        self.b = os.path.join(self.tmp.name, "data", "settings.json")
        self.logs = []
        self.store = SettingsStore([self.a, self.b], log=self.logs.append)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_writes_every_copy_and_merges(self):
        self.assertTrue(self.store.save(model="base.en"))
        self.assertTrue(self.store.save(activator=["pad:R4", "pad:R5"]))
        for path in (self.a, self.b):
            with open(path) as f:
                self.assertEqual(json.load(f), {"model": "base.en", "activator": ["pad:R4", "pad:R5"]})
        self.assertEqual(self.store.load()["model"], "base.en")      # saving one never drops another

    def test_survives_losing_one_copy(self):
        self.store.save(activator=["key:67"])
        os.remove(self.a)
        self.assertEqual(self.store.load()["activator"], ["key:67"])
        os.remove(self.b)
        self.assertEqual(self.store.load(), {})

    def test_survives_an_empty_or_corrupt_copy_and_keeps_it(self):
        self.store.save(activator=["key:67"])
        with open(self.a, "w") as f:
            f.write("")                                              # what an unflushed write can leave
        self.assertEqual(self.store.load()["activator"], ["key:67"])
        self.assertTrue(os.path.exists(self.a + ".corrupt"))         # kept, not silently overwritten
        self.assertTrue(any("unreadable" in m for m in self.logs))
        self.store.save(model="small.en")                            # and the next save repairs both copies
        self.assertEqual(json.load(open(self.a)), {"activator": ["key:67"], "model": "small.en"})

    def test_newest_copy_wins_per_setting_older_fills_gaps(self):
        os.makedirs(os.path.dirname(self.a))
        os.makedirs(os.path.dirname(self.b))
        with open(self.a, "w") as f:
            json.dump({"model": "tiny.en", "activator": ["key:1"]}, f)
        with open(self.b, "w") as f:
            json.dump({"model": "base.en"}, f)
        now = time.time()
        os.utime(self.a, (now - 100, now - 100))
        os.utime(self.b, (now, now))
        merged = self.store.load()
        self.assertEqual(merged["model"], "base.en")                 # newer file wins
        self.assertEqual(merged["activator"], ["key:1"])             # older file fills the gap

    def test_one_unwritable_location_does_not_lose_the_setting(self):
        blocker = os.path.join(self.tmp.name, "blocked")
        open(blocker, "w").close()                                   # a FILE where a folder is needed
        store = SettingsStore([os.path.join(blocker, "settings.json"), self.b], log=self.logs.append)
        self.assertTrue(store.save(activator=["key:67"]))
        self.assertEqual(store.load()["activator"], ["key:67"])
        self.assertTrue(any("could not write" in m for m in self.logs))

    def test_nothing_writable_reports_failure(self):
        blocker = os.path.join(self.tmp.name, "blocked")
        open(blocker, "w").close()
        store = SettingsStore([os.path.join(blocker, "a.json")], log=self.logs.append)
        self.assertFalse(store.save(x=1))
        self.assertTrue(any("NOT saved" in m for m in self.logs))

    def test_no_temp_files_left_behind(self):
        self.store.save(a=1)
        self.store.save(b=2)
        for folder in (os.path.dirname(self.a), os.path.dirname(self.b)):
            self.assertEqual(os.listdir(folder), ["settings.json"])

    def test_non_dict_json_is_treated_as_corrupt(self):
        os.makedirs(os.path.dirname(self.a))
        with open(self.a, "w") as f:
            f.write("[1, 2, 3]")
        self.assertEqual(self.store.load(), {})


if __name__ == "__main__":
    unittest.main()
