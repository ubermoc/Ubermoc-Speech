# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Tests for py_modules/ubermoc_apps.py: per-application activator, model and typing speed.
Run: python3 -m unittest discover -s tests -v

The rule these tests hold: a field that is absent, empty or unusable follows the Global
options, and the stored dict is never changed in place.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
import ubermoc_apps as ua  # noqa: E402
import ubermoc_typing as ut  # noqa: E402

GLOBAL_TOKENS = ["key:29", "key:67"]      # Left Ctrl + F9
MODEL = "small.en"
NO_MODEL = ""


def installed(names):
    """A stand for "this model file is on the computer" (main.py checks size on disk)."""
    return lambda name: name in names


class OldSettings(unittest.TestCase):
    """A settings file written by an older version has none of the new fields."""

    def test_an_override_without_the_new_fields_loads_unchanged(self):
        raw = {"570": {"name": "Dota 2", "kind": "steam",
                       "before": {"mode": "none"}, "after": {"mode": "enter"}, "symbols": "words"}}
        ov = ut.normalize_overrides(raw)["570"]
        self.assertEqual(ov["before"], {"mode": "none", "keys": []})
        self.assertEqual(ov["after"], {"mode": "enter", "keys": []})
        self.assertEqual(ov["symbols"], "words")
        self.assertNotIn("activator", ov)
        self.assertNotIn("model", ov)
        self.assertNotIn("speed", ov)

    def test_an_old_override_falls_back_to_the_global_options(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2"}})["570"]
        self.assertEqual(ua.activator_for(ov, GLOBAL_TOKENS), GLOBAL_TOKENS)
        self.assertEqual(ua.speed_for(ov, "normal"), "normal")
        self.assertEqual(ua.model_for(ov, MODEL, installed([MODEL])), MODEL)

    def test_an_app_without_an_override_uses_the_global_options(self):
        for ov in (None, {}):
            self.assertEqual(ua.activator_for(ov, GLOBAL_TOKENS), GLOBAL_TOKENS)
            self.assertEqual(ua.speed_for(ov, "slow"), "slow")
            self.assertEqual(ua.model_for(ov, MODEL, installed([MODEL])), MODEL)


class Validating(unittest.TestCase):
    def test_valid_fields_are_kept(self):
        entry = {"activator": ["pad:R4", "pad:R5"], "model": "large-v3-turbo", "speed": "slow"}
        self.assertEqual(ua.normalize_extra(entry),
                         {"activator": ["pad:R4", "pad:R5"], "model": "large-v3-turbo", "speed": "slow"})

    def test_missing_and_junk_fields_are_dropped(self):
        entry = {"activator": "not a list", "model": "unheard-of-model", "speed": "turbo"}
        self.assertEqual(ua.normalize_extra(entry), {})

    def test_empty_and_wrong_types_are_dropped(self):
        for value in (None, "", 0, [], {}, "570"):
            self.assertEqual(ua.normalize_extra({"activator": value}), {})
        for value in (None, "", 0, [], {}, "small.en ", "SMALL.EN"):
            self.assertEqual(ua.normalize_extra({"model": value}), {})
        for value in (None, "", "FAST", "faster", 1, ["fast"]):
            self.assertEqual(ua.normalize_extra({"speed": value}), {})

    def test_junk_tokens_inside_a_combination_are_dropped(self):
        self.assertEqual(ua.normalize_extra({"activator": ["key:29", "nonsense", 3, "pad:R4"]}),
                         {"activator": ["key:29", "pad:R4"]})

    def test_the_speeds_match_the_typing_timings(self):
        # The panel offers exactly the speeds ubermoc_typing has timings for.
        self.assertEqual(sorted(ua.SPEEDS), sorted(ut.TIMING))


class SettingFields(unittest.TestCase):
    def setUp(self):
        self.stored = {"570": {"name": "Dota 2", "kind": "steam",
                               "before": {"mode": "enter"}, "after": {"mode": "enter"}}}

    def test_setting_a_field(self):
        out = ua.set_field(self.stored, "570", "speed", "slow")
        self.assertEqual(out["570"]["speed"], "slow")
        self.assertEqual(out["570"]["before"], {"mode": "enter"})   # the other fields are carried over
        self.assertEqual(out["570"]["name"], "Dota 2")

    def test_setting_every_field(self):
        out = ua.set_field(self.stored, "570", "activator", ["key:29", "key:67"])
        self.assertEqual(out["570"]["activator"], ["key:29", "key:67"])
        out = ua.set_field(self.stored, "570", "model", "parakeet-tdt-0.6b-v2")
        self.assertEqual(out["570"]["model"], "parakeet-tdt-0.6b-v2")

    def test_the_stored_dict_is_never_changed_in_place(self):
        out = ua.set_field(self.stored, "570", "speed", "slow")
        out["570"]["before"] = {"mode": "none", "keys": []}
        self.assertEqual(self.stored["570"]["before"], {"mode": "enter"})      # input untouched
        self.assertNotIn("speed", self.stored["570"])                          # not saved by the copy
        self.assertEqual(ua.set_field(self.stored, "570", "speed", "fast")["570"]["speed"], "fast")

    def test_clearing_follows_global(self):
        for value in ("", None, [], "not a list", ["nonsense"]):
            out = ua.set_field({"570": {"name": "x", "activator": ["key:29"]}}, "570", "activator", value)
            self.assertNotIn("activator", out["570"])
        for value in ("", None, "unknown-model"):
            out = ua.set_field({"570": {"name": "x", "model": "small.en"}}, "570", "model", value)
            self.assertNotIn("model", out["570"])
        for value in ("", None, "turbo", "FAST"):
            out = ua.set_field({"570": {"name": "x", "speed": "slow"}}, "570", "speed", value)
            self.assertNotIn("speed", out["570"])

    def test_unknown_field_appid_or_entry_changes_nothing(self):
        self.assertEqual(ua.set_field(self.stored, "570", "volume", "slow"), self.stored)
        self.assertEqual(ua.set_field(self.stored, "999", "speed", "slow"), self.stored)
        self.assertEqual(ua.set_field(self.stored, "570", "speed", "slow"),
                         {"570": {**self.stored["570"], "speed": "slow"}})
        self.assertEqual(ua.set_field({"1": "not a dict"}, "1", "speed", "slow"), {})

    def test_a_combination_and_a_model_survive_a_round_trip_through_normalize(self):
        out = ua.set_field(self.stored, "570", "activator", ["pad:R4", "pad:R5"])
        out = ua.set_field(out, "570", "model", "small.en")
        out = ua.set_field(out, "570", "speed", "fast")
        ov = ut.normalize_overrides(out)["570"]
        self.assertEqual(ov["activator"], ["pad:R4", "pad:R5"])
        self.assertEqual(ov["model"], "small.en")
        self.assertEqual(ov["speed"], "fast")
        self.assertEqual(ov["before"], {"mode": "enter", "keys": []})


class Choosing(unittest.TestCase):
    def test_the_app_activator_replaces_the_global_one(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "activator": ["pad:R4", "pad:R5"]}})["570"]
        self.assertEqual(ua.activator_for(ov, GLOBAL_TOKENS), ["pad:R4", "pad:R5"])

    def test_an_app_with_no_activator_field_uses_the_global_activator(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "activator": []}})["570"]
        self.assertEqual(ua.activator_for(ov, GLOBAL_TOKENS), GLOBAL_TOKENS)

    def test_an_app_with_only_junk_tokens_uses_the_global_activator(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "activator": ["nonsense", 7, ""]}})["570"]
        self.assertEqual(ua.activator_for(ov, GLOBAL_TOKENS), GLOBAL_TOKENS)

    def test_no_global_activator_stays_no_activator(self):
        self.assertEqual(ua.activator_for(None, []), [])

    def test_the_speed_is_the_app_s_when_it_is_set(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "speed": "slow"}})["570"]
        self.assertEqual(ua.speed_for(ov, "fast"), "slow")

    def test_an_unusable_global_speed_gives_normal(self):
        for value in (None, "turbo", "FAST"):
            self.assertEqual(ua.speed_for(None, value), "normal")

    def test_the_app_model_is_used_when_it_is_downloaded(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "model": "large-v3"}})["570"]
        have = installed(["small.en", "large-v3"])
        self.assertEqual(ua.model_for(ov, "small.en", have), "large-v3")

    def test_a_deleted_model_falls_back_to_the_global_model(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "model": "large-v3"}})["570"]
        self.assertEqual(ua.model_for(ov, "small.en", installed(["small.en"])), "small.en")
        # the field is kept, so downloading the model again makes the app use it with nothing to fix
        have = installed(["small.en", "large-v3"])
        self.assertEqual(ua.model_for(ov, "small.en", have), "large-v3")

    def test_nothing_downloaded_at_all_gives_no_model(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "model": "large-v3"}})["570"]
        self.assertEqual(ua.model_for(ov, NO_MODEL, installed([])), NO_MODEL)

    def test_the_three_lookups_together_for_one_running_app(self):
        settings = {"activator": GLOBAL_TOKENS, "type_speed": "normal", "model": "small.en",
                    "overrides": {"570": {"name": "Dota 2", "model": "large-v3", "speed": "slow"}}}
        ov = ut.normalize_overrides(settings["overrides"])["570"]
        have = installed(["small.en", "large-v3"])
        self.assertEqual(ua.activator_for(ov, settings["activator"]), GLOBAL_TOKENS)
        self.assertEqual(ua.speed_for(ov, settings["type_speed"]), "slow")
        self.assertEqual(ua.model_for(ov, settings["model"], have), "large-v3")


class NewPerAppFields(unittest.TestCase):
    """The options that were only global before: typing method, spoken symbols, rumble on/off,
    rumble intensity and the voice detector. Each follows the Global option when unset."""

    def test_valid_new_fields_are_kept(self):
        entry = {"method": "steam", "symbols": "symbols", "rumble": False,
                 "rumble_level": 7, "vad": False}
        self.assertEqual(ua.normalize_extra(entry), entry)

    def test_junk_new_fields_are_dropped(self):
        for value in (None, "", "fast", "Steam", "STEAM", 1, ["steam"]):
            self.assertEqual(ua.normalize_extra({"method": value}), {})
        for value in (None, "", "symbol", "WORDS", 1, ["words"]):
            self.assertEqual(ua.normalize_extra({"symbols": value}), {})
        for value in (None, "", "true", "off", 0, 1, [False]):
            self.assertEqual(ua.normalize_extra({"rumble": value}), {})
        for value in (None, "", "5", 0, 11, -1, True, False, 2.5):
            self.assertEqual(ua.normalize_extra({"rumble_level": value}), {})
        for value in (None, "", "on", "off", 1, 0):
            self.assertEqual(ua.normalize_extra({"vad": value}), {})

    def test_setting_a_new_field_keeps_the_others(self):
        stored = {"570": {"name": "Dota 2", "kind": "steam", "model": "small.en",
                           "before": {"mode": "enter"}, "after": {"mode": "enter"}}}
        out = ua.set_field(stored, "570", "method", "steam")
        out = ua.set_field(out, "570", "rumble", False)
        out = ua.set_field(out, "570", "rumble_level", 3)
        out = ua.set_field(out, "570", "vad", True)
        out = ua.set_field(out, "570", "symbols", "symbols")
        self.assertEqual(ua.normalize_extra(out["570"]),
                         {"model": "small.en", "method": "steam", "rumble": False,
                          "rumble_level": 3, "vad": True, "symbols": "symbols"})
        self.assertNotIn("method", stored["570"])          # the stored dict is never changed in place

    def test_clearing_a_new_field_follows_global(self):
        for value in ("", None, "Steam"):
            self.assertNotIn("method", ua.set_field({"570": {"name": "x", "method": "steam"}}, "570", "method", value)["570"])
        for value in ("", None, "off", "false"):
            self.assertNotIn("rumble", ua.set_field({"570": {"name": "x", "rumble": False}}, "570", "rumble", value)["570"])
        for value in ("", None, "3", 0, 11):
            self.assertNotIn("rumble_level", ua.set_field({"570": {"name": "x", "rumble_level": 5}}, "570", "rumble_level", value)["570"])
        for value in ("", None, "on"):
            self.assertNotIn("vad", ua.set_field({"570": {"name": "x", "vad": True}}, "570", "vad", value)["570"])

    def test_the_panel_can_set_every_field_in_FIELDS(self):
        # the panel uses one callable for all of them, so every field it shows must be accepted here
        self.assertEqual(sorted(ua.FIELDS),
                         ["activator", "method", "model", "rumble", "rumble_level", "speed", "symbols", "vad"])

    def test_the_method_is_the_app_s_when_it_is_set(self):
        ov = {"method": "steam"}
        self.assertEqual(ua.method_for(ov, "keyboard"), "steam")
        self.assertEqual(ua.method_for({}, "keyboard"), "keyboard")
        self.assertEqual(ua.method_for(None, "steam"), "steam")
        for bad in (None, "fast", "Steam"):
            self.assertEqual(ua.method_for(None, bad), "keyboard")

    def test_the_app_rumble_switch_and_level_beat_the_global_ones(self):
        self.assertEqual(ua.rumble_for({"rumble": False}, True), False)
        self.assertEqual(ua.rumble_for({"rumble": True}, False), True)
        self.assertEqual(ua.rumble_for({}, True), True)
        self.assertEqual(ua.rumble_for(None, False), False)
        self.assertEqual(ua.rumble_for(None, None), True)
        self.assertEqual(ua.level_for({"rumble_level": 2}, 8), 2)
        self.assertEqual(ua.level_for({}, 8), 8)
        self.assertEqual(ua.level_for(None, 42), 10)      # clamped to the panel's range
        self.assertEqual(ua.level_for(None, None), 1)

    def test_the_app_voice_detector_beats_the_global_choice(self):
        self.assertEqual(ua.vad_for({"vad": False}, True), False)
        self.assertEqual(ua.vad_for({"vad": True}, False), True)
        self.assertEqual(ua.vad_for({}, True), True)
        self.assertEqual(ua.vad_for(None, False), False)


class HiddenAppsAndSwitches(unittest.TestCase):
    def test_only_numeric_appids_stay_true(self):
        raw = {"570": True, "730": True, "abc": True, "88": False, "99": "yes"}
        self.assertEqual(ua.normalize_hidden(raw), {"570": True, "730": True})

    def test_junk_and_absent_mean_nothing_is_hidden(self):
        for raw in (None, [], {}, "yes", {"570": "yes"}, {"570": 1}):
            self.assertEqual(ua.normalize_hidden(raw), {})

    def test_a_switch_is_on_only_when_stored_true(self):
        for value in (True,):
            self.assertIs(ua.flag_of(value), True)
        for value in (None, False, "true", 1, []):
            self.assertIs(ua.flag_of(value), False)

    def test_an_old_settings_file_has_no_hidden_apps_and_no_switches(self):
        s = {"activator": GLOBAL_TOKENS, "type_speed": "normal", "model": "small.en",
             "overrides": {"570": {"name": "Dota 2", "kind": "steam"}}}
        self.assertEqual(ua.normalize_hidden(s.get("hidden")), {})
        self.assertIs(ua.flag_of(s.get("show_hidden")), False)
        self.assertIs(ua.flag_of(s.get("keep_loaded")), False)
        ov = ut.normalize_overrides(s["overrides"])["570"]
        self.assertEqual(ua.method_for(ov, "keyboard"), "keyboard")
        self.assertEqual(ua.rumble_for(ov, True), True)
        self.assertEqual(ua.level_for(ov, 3), 3)
        self.assertEqual(ua.vad_for(ov, True), True)


class ConnectedToThePanelList(unittest.TestCase):
    """The panel reads one dict per app; the fields it needs must be in it."""

    def test_the_normalized_override_carries_every_field_the_panel_shows(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2", "kind": "steam",
                                             "activator": ["pad:R4"], "model": "small.en",
                                             "speed": "fast", "symbols": "symbols", "method": "steam",
                                             "rumble": False, "rumble_level": 4, "vad": True}})["570"]
        self.assertEqual(sorted(ov), ["activator", "after", "before", "kind", "method", "model",
                                      "name", "rumble", "rumble_level", "speed", "symbols", "vad"])

    def test_an_override_that_only_has_a_name_is_still_valid(self):
        ov = ut.normalize_overrides({"570": {"name": "Dota 2"}})["570"]
        self.assertEqual(sorted(ov), ["after", "before", "kind", "name"])

    def test_the_count_cap_still_applies_with_the_new_fields(self):
        raw = {str(i): {"name": f"g{i}", "speed": "slow"} for i in range(ut.MAX_OVERRIDES + 20)}
        out = ut.normalize_overrides(raw)
        self.assertEqual(len(out), ut.MAX_OVERRIDES)
        self.assertTrue(all(o["speed"] == "slow" for o in out.values()))


if __name__ == "__main__":
    unittest.main()
