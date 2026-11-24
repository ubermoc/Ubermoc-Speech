# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Per-application wiring in main.py (the plugin backend), tested without Decky.

main.py imports `decky`, which only exists inside Decky Loader, so the backend cannot be
imported here. The loader below builds the module with a stand for that runtime, in the shape
Decky's own source shows. The input hub is replaced by a recorder that notes which combination
the plugin armed, and the typing hardware is bypassed by replacing the plugin's own typing
methods, so the rules (which activator, which model, which speed) can be checked on this machine.

The real server, the real microphone and the real keyboard are NOT used here:
python3 scripts/integration_smoke.py is the test that runs the real servers and models.

Run: python3 -m unittest discover -s tests -v
"""
import asyncio
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
import ubermoc_apps  # noqa: E402
import ubermoc_input  # noqa: E402
import ubermoc_typing  # noqa: E402

GLOBAL_ACT = ["key:29", "key:67"]            # Left Ctrl + F9
APP_ACT = ["pad:R4", "pad:R5"]               # R4 + R5
DOTA = "570"


def load_backend(tmp):
    """main.py loaded with a stand for the Decky runtime.

    FAKE(C4): Decky's own source (decky-loader,
    backend/decky_loader/plugin/imports/decky.py) shows the module it hands a python plugin:
    `DECKY_USER_HOME`, `DECKY_PLUGIN_SETTINGS_DIR`, `DECKY_PLUGIN_DIR` and
    `DECKY_PLUGIN_LOG_DIR` are each a `str` from the matching environment variable, `logger`
    is a `logging.Logger`, and `emit(event, *args)` is async (claims C5 and C6). Nothing here is stricter than
    that, and nothing extra is added.
    """
    decky = types.ModuleType("decky")
    decky.DECKY_USER_HOME = tmp
    decky.DECKY_PLUGIN_DIR = ROOT
    decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(tmp, "settings")
    decky.DECKY_PLUGIN_LOG_DIR = os.path.join(tmp, "logs")
    decky.logger = logging.getLogger("test-backend")
    decky.logger.addHandler(logging.NullHandler())

    async def emit(event, *args):
        return None

    decky.emit = emit
    sys.modules["decky"] = decky
    spec = importlib.util.spec_from_file_location("plugin_main", os.path.join(ROOT, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHub:
    """Stands for ubermoc_input.InputHub (the plugin's own class): records which combination
    the plugin armed, in the same shape the real hub reports it (sorted token list)."""

    def __init__(self, tokens=()):
        self._activator = frozenset(tokens)
        self.armed = []

    def set_activator(self, tokens):
        self._activator = frozenset(tokens)
        self.armed.append(ubermoc_input.valid_tokens(tokens))

    @property
    def activator(self):
        return ubermoc_input.valid_tokens(self._activator)

    @property
    def active(self):
        return False

    def any_held(self, tokens):
        return False

    def begin_capture(self, timeout=15.0, on_done=None, purpose="activator"):
        # FAKE(C7): ubermoc_input.InputHub.begin_capture(timeout=15.0, on_done=None,
        # purpose="activator") starts a recording; this keeps the same arguments.
        # FAKE(C8): the real capture_status returns state, tokens, label and purpose,
        # which this copy returns.
        self.state, self.tokens, self.purpose, self.on_done = "active", [], purpose, on_done

    def finish(self, tokens):
        """Stands for the user letting go: the hub stores the combination and calls on_done."""
        self.tokens = ubermoc_input.valid_tokens(tokens)
        self.state = "done"
        if self.on_done:
            self.on_done(self.tokens)

    def cancel_capture(self):
        self.state = "cancelled"

    def capture_status(self):
        return {"state": self.state, "tokens": self.tokens,
                "label": ubermoc_input.describe(self.tokens), "purpose": self.purpose}


def live(proc):
    """A real subprocess that is running, so the plugin sees a server that is up."""
    return proc


class Fixtures(unittest.TestCase):
    """The backend with a stand for the Decky runtime, the input hub replaced by a recorder, and
    no real microphone, keyboard, gamepad or speech server. Shared by every test class here."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = load_backend(self.tmp.name)
        self.p = self.mod.Plugin()
        self.p.hub = FakeHub()
        self.p.panel_seen = 0.0
        self.p.rec_model = ""
        self.p.running_app = None
        # No microphone tool here: start_recording picks the model and then stops, so no
        # real recording is ever started by these tests.
        self.mod.shutil.which = lambda name: None
        # The plugin's own virtual-device preparation is replaced with a no-op: the real
        # keyboard/gamepad creation is only exercised by scripts/integration_smoke.py on the
        # Deck. These tests check which settings were saved, not what the devices feel like.
        self.mod.Plugin._prepare_outputs = lambda self: asyncio.sleep(0)

    def tearDown(self):
        for proc in (self.p.server_proc, getattr(self.p, "extra_proc", None)) + tuple(getattr(self, "fakes", [])):
            if proc and proc.poll() is None:
                proc.kill()
            if proc:
                proc.wait()      # reap it, so no "subprocess is still running" warning is left
        self.tmp.cleanup()

    def settings(self, **patch):
        self.p._save_settings(**patch)

    def run_all(self, coro):
        """Run `coro`, then everything it started in the background, so a background model load
        the plugin kicks off is recorded the way it happens when the plugin is running."""
        async def go():
            await coro
            while True:
                pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                if not pending:
                    return
                await asyncio.gather(*pending, return_exceptions=True)
        return asyncio.run(go())


class Backend(Fixtures):
    def finish_capture(self, tokens):
        """The user lets go: the hub reports the combination, as it does inside Decky's loop."""
        async def go():
            self.p.hub.finish(tokens)
            await asyncio.sleep(0)      # let the task the capture started run
        asyncio.run(go())

    # ---------- the activator that is armed ----------

    def test_the_running_app_with_its_own_activator_is_armed(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT}})
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(APP_ACT))
        self.assertNotEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))

    def test_an_app_without_an_activator_override_keeps_the_global_activator(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT}})
        asyncio.run(self.p.set_running_app("999", "Some other game"))
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))

    def test_an_app_with_only_junk_in_its_activator_uses_the_global_one(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": ["nonsense", 3, ""]}})
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))

    def test_no_steam_interface_desktop_mode_always_uses_the_global_activator(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT}})
        asyncio.run(self.p.set_running_app("", ""))
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))

    def test_switching_apps_rearms_the_activator_of_the_new_app(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT},
                                 "1234": {"name": "Adverture", "activator": ["mouse:275"]}})
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(APP_ACT))
        asyncio.run(self.p.set_running_app("1234", "Adventurer"))
        self.assertEqual(self.p.hub.activator, ["mouse:275"])
        asyncio.run(self.p.set_running_app("1234", "Adventurer"))      # same app: no change
        asyncio.run(self.p.set_running_app("999", "Other"))
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))

    def test_clearing_the_global_activator_leaves_the_running_app_s_armed(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT}})
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        asyncio.run(self.p.clear_activator())
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(APP_ACT))
        self.assertEqual(ubermoc_input.valid_tokens(self.p._load_settings().get("activator")), [])

    def test_recording_a_combination_for_the_running_app_arms_it_and_saves_it(self):
        self.settings(activator=GLOBAL_ACT, overrides={DOTA: {"name": "Dota 2"}})
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.p._captured(["pad:L4", "pad:L5"], DOTA)
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(["pad:L4", "pad:L5"]))
        saved = self.p._load_settings()["overrides"][DOTA]["activator"]
        self.assertEqual(saved, ubermoc_input.valid_tokens(["pad:L4", "pad:L5"]))

    def test_recording_the_global_combination_while_an_app_is_running_leaves_the_app_s(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT}})
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.p._captured(["key:57"], "")
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(APP_ACT))
        self.assertEqual(ubermoc_input.valid_tokens(self.p._load_settings()["activator"]),
                         ubermoc_input.valid_tokens(["key:57"]))

    def test_the_key_route_records_an_activator_for_the_app_the_panel_chose(self):
        self.settings(activator=GLOBAL_ACT, overrides={DOTA: {"name": "Dota 2"}})
        self.p._apply_activator()
        r = asyncio.run(self.p.start_key_capture("activator", DOTA))
        self.assertTrue(r["ok"])
        self.assertEqual(self.p.hub.purpose, f"activator@{DOTA}")     # the panel shows this
        self.finish_capture(APP_ACT)                         # the user lets go
        saved = self.p._load_settings()["overrides"][DOTA]["activator"]
        self.assertEqual(ubermoc_apps.activator_for({"activator": saved}, GLOBAL_ACT), APP_ACT)
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))  # Dota is not running

    def test_recording_an_activator_for_the_running_app_rearms_it_at_once(self):
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.settings(activator=GLOBAL_ACT, overrides={DOTA: {"name": "Dota 2"}})
        self.p._apply_activator()
        asyncio.run(self.p.start_key_capture("activator", DOTA))
        self.finish_capture(APP_ACT)
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(APP_ACT))

    def test_the_key_route_still_records_before_and_after_keys(self):
        self.settings(overrides={DOTA: {"name": "Dota 2"}})
        r = asyncio.run(self.p.start_key_capture("before", DOTA))
        self.assertTrue(r["ok"])
        self.assertEqual(self.p.hub.purpose, f"before@{DOTA}")
        self.finish_capture(APP_ACT)
        saved = self.p._load_settings()["overrides"][DOTA]["before"]
        self.assertEqual(saved["mode"], "custom")
        self.assertEqual(ubermoc_input.valid_tokens(saved["keys"]), APP_ACT)

    def test_an_app_that_is_not_running_is_not_armed_and_its_tokens_are_kept(self):
        self.settings(activator=GLOBAL_ACT)
        self.p._apply_activator()                      # what the plugin does when it loads
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))
        self.p._captured(["pad:R4"], "999")            # 999 has no override: nothing is stored
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))
        self.assertNotIn("999", self.p._load_settings().get("overrides", {}))

    def test_a_recording_in_progress_is_not_rearmed_mid_dictation(self):
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT}})
        self.p.phase = "recording"
        self.p.by_activator = True
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.assertEqual(self.p.hub.activator, [])
        self.p.phase = "idle"
        self.p.by_activator = False
        self.p._apply_activator()
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(APP_ACT))

    # ---------- the model used for dictation ----------

    def test_dictation_starts_with_the_running_app_s_model(self):
        self.settings(model="small.en",
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda name: name in ("small.en", "large-v3")     # which files are on disk
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        r = asyncio.run(self.p.start_recording())
        self.assertEqual(self.p.rec_model, "large-v3")
        self.assertIsNone(self.p.record_proc)       # nothing was recorded on this machine
        self.assertFalse(r.get("ok"))              # it stopped at the missing recorder

    def test_an_app_model_that_is_not_downloaded_falls_back_to_the_global_model(self):
        self.settings(model="small.en",
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda name: name == "small.en"
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        asyncio.run(self.p.start_recording())
        self.assertEqual(self.p.rec_model, "small.en")

    def test_an_app_with_no_model_setting_uses_the_global_model(self):
        self.settings(model="small.en", overrides={DOTA: {"name": "Dota 2"}})
        self.p._model_ok = lambda name: name == "small.en"
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        asyncio.run(self.p.start_recording())
        self.assertEqual(self.p.rec_model, "small.en")

    def test_nothing_downloaded_is_reported_as_no_model(self):
        self.settings(model="small.en", overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda name: False
        r = asyncio.run(self.p.start_recording())
        self.assertEqual(self.p.rec_model, "")
        self.assertIn("no model selected", r.get("error", ""))

    def test_the_panel_is_told_the_model_is_loading(self):
        self.p.phase = "recording"
        self.p.rec_model = "large-v3"
        self.p.server_proc = None
        self.p.server_model = ""
        st = asyncio.run(self.p.get_state())
        self.assertEqual((st["model"], st["loading_model"]), ("large-v3", True))
        proc = subprocess.Popen(["sleep", "30"])
        self.p.server_proc = live(proc)
        self.p.server_model = "large-v3"      # the server that is up runs THIS model
        st = asyncio.run(self.p.get_state())
        self.assertEqual(st["loading_model"], False)
        proc.kill()
        proc.wait()
        self.p.server_model = ""
        self.p.phase = "idle"

    def test_a_server_running_a_different_model_does_not_answer_for_this_model(self):
        # the global model is loaded and an app wants its own: the app still has to wait
        self.p.phase = "recording"
        self.p.rec_model = "large-v3"
        proc = subprocess.Popen(["sleep", "30"])
        self.p.server_proc = live(proc)
        self.p.server_model = "small.en"
        st = asyncio.run(self.p.get_state())
        self.assertEqual(st["loading_model"], True)
        self.assertEqual(st["loaded_models"], ["small.en"])
        proc.kill()
        proc.wait()
        self.p.server_model = ""
        self.p.phase = "idle"

    # ---------- typing speed ----------

    def typing_speed(self, settings_overrides, running, global_speed):
        self.settings(activator=[], model="small.en", type_speed=global_speed,
                      overrides=settings_overrides)
        asyncio.run(self.p.set_running_app(running, "app"))
        self.p._type_blocking = lambda text, delay: {"typed": len(text), "skipped": 0}
        async def no_press(tokens, hold):            # no virtual devices on this machine
            return ""
        self.p._press = no_press
        r = asyncio.run(self.p._deliver_text("hello there", force=True))
        return r.get("speed"), r.get("status")

    def test_the_app_s_speed_is_used(self):
        speed, status = self.typing_speed({DOTA: {"name": "Dota 2", "speed": "slow"}}, DOTA, "normal")
        self.assertEqual((speed, status), ("slow", "typed"))

    def test_an_app_with_no_speed_uses_the_global_speed(self):
        speed, status = self.typing_speed({DOTA: {"name": "Dota 2"}}, DOTA, "fast")
        self.assertEqual((speed, status), ("fast", "typed"))

    def test_an_unusable_app_speed_falls_back_to_the_global_speed(self):
        speed, status = self.typing_speed({DOTA: {"name": "Dota 2", "speed": "turbo"}}, DOTA, "normal")
        self.assertEqual((speed, status), ("normal", "typed"))

    # ---------- the panel's view and the setters ----------

    def test_the_panel_sees_the_new_fields_and_the_global_values(self):
        self.settings(activator=GLOBAL_ACT, model="small.en", type_speed="normal",
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT,
                                        "model": "large-v3", "speed": "slow"},
                                 "1234": {"name": "Adventurer"}})
        self.p._model_ok = lambda name: name in ("small.en", "large-v3")
        out = asyncio.run(self.p.get_overrides())
        by = {o["appid"]: o for o in out["overrides"]}
        self.assertEqual(by[DOTA]["activator"], ubermoc_input.valid_tokens(APP_ACT))
        self.assertEqual(by[DOTA]["activator_label"], "R4 + R5")
        self.assertEqual(by[DOTA]["model"], "large-v3")
        self.assertEqual(by[DOTA]["model_installed"], True)
        self.assertEqual(by[DOTA]["speed"], "slow")
        self.assertEqual(by["1234"], {**by["1234"], "activator": [], "model": "", "speed": "",
                                      "activator_label": "", "model_installed": False, "model_loaded": False,
                                      "method": "", "rumble": None, "rumble_level": None, "vad": None,
                                      "hidden": False})
        self.assertEqual(out["global"], {"activator_label": "Left Ctrl + F9",
                                         "speed": "normal", "model": "small.en",
                                         "secondary_model": "", "secondary_installed": False,
                                         "symbols": "words", "method": "keyboard",
                                         "rumble": True, "rumble_level": 3, "vad": True,
                                         "vad_supported": True})
        self.assertEqual((out["hidden"], out["show_hidden"], out["keep_loaded"]), ([], False, False))

    def test_setting_a_field_and_clearing_it_back_to_global(self):
        self.settings(overrides={DOTA: {"name": "Dota 2"}})
        r = asyncio.run(self.p.set_override_field(DOTA, "speed", "slow"))
        self.assertEqual(r["ok"], True)
        self.assertEqual({o["appid"]: o for o in r["overrides"]}[DOTA]["speed"], "slow")
        r = asyncio.run(self.p.set_override_field(DOTA, "speed", ""))
        self.assertEqual({o["appid"]: o for o in r["overrides"]}[DOTA]["speed"], "")

    def test_a_model_change_stops_the_server_so_the_next_dictation_loads_it(self):
        self.settings(model="small.en", overrides={DOTA: {"name": "Dota 2"}})
        proc = subprocess.Popen(["sleep", "30"])
        self.p.server_proc = live(proc)
        self.p.server_model = "large-v3"
        asyncio.run(self.p.set_override_field(DOTA, "model", "large-v3"))
        self.assertIsNone(self.p.server_proc)
        self.assertEqual(self.p.server_model, "")

    def test_the_model_field_is_not_stored_if_the_model_is_unknown(self):
        self.settings(overrides={DOTA: {"name": "Dota 2"}})
        r = asyncio.run(self.p.set_override_field(DOTA, "model", "not-a-model"))
        self.assertEqual({o["appid"]: o for o in r["overrides"]}[DOTA]["model"], "")

    def test_an_app_without_an_override_and_an_unknown_field_are_reported_as_failures(self):
        self.settings(overrides={DOTA: {"name": "Dota 2"}})
        r = asyncio.run(self.p.set_override_field("999", "speed", "slow"))
        self.assertEqual((r["ok"], r["error"]), (False, "no override for that app"))
        r = asyncio.run(self.p.set_override_field(DOTA, "volume", "loud"))
        self.assertEqual((r["ok"], r["error"]), (False, "cannot change volume"))

    def test_deleting_a_model_an_app_has_loaded_frees_it_and_the_app_falls_back(self):
        self.settings(model="small.en", overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        mdir = self.p._models_dir()
        os.makedirs(mdir, exist_ok=True)
        name = self.mod.ubermoc_models.model_filename("large-v3")
        with open(os.path.join(mdir, name), "wb") as f:
            f.write(b"x" * 1000)
        self.p.server_model = "large-v3"
        proc = subprocess.Popen(["sleep", "30"])
        self.p.server_proc = live(proc)
        r = asyncio.run(self.p.delete_model("large-v3"))
        self.assertEqual(r.get("ok"), True)
        self.assertIsNone(self.p.server_proc)
        self.p._model_ok = lambda n: n == "small.en"
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        asyncio.run(self.p.start_recording())
        self.assertEqual(self.p.rec_model, "small.en")

    def test_an_old_settings_file_without_the_new_fields_loads_and_behaves_as_before(self):
        import json
        path = os.path.join(self.p._data_dir(), "settings.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"model": "small.en", "activator": GLOBAL_ACT, "type_speed": "normal",
                       "before": {"mode": "enter"}, "after": {"mode": "enter"},
                       "overrides": {DOTA: {"name": "Dota 2", "kind": "steam",
                                            "before": {"mode": "none"}, "after": {"mode": "enter"},
                                            "symbols": "words"}}}, f)
        s = self.p._load_settings()
        ov = self.mod.ubermoc_typing.normalize_overrides(s["overrides"])[DOTA]
        self.assertEqual(sorted(ov), ["after", "before", "kind", "name", "symbols"])
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(GLOBAL_ACT))
        self.p._model_ok = lambda name: name == "small.en"
        asyncio.run(self.p.start_recording())
        self.assertEqual(self.p.rec_model, "small.en")


class FakeRumbler:
    """Stands for ubermoc_rumble.Rumbler (the plugin's own class): records the pattern and the
    step it was asked for, in the shape `play(which, level)` of the real class takes and returns."""

    def __init__(self):
        self.played = []

    def play(self, which, level=3):
        self.played.append((which, level))
        return True


class TypingMethodPerApp(Fixtures):
    """The typing method was a global option; an app can now choose its own."""

    def way(self, overrides, running, global_method):
        self.settings(activator=[], model="small.en", type_speed="normal",
                      type_method=global_method, overrides=overrides)
        asyncio.run(self.p.set_running_app(running, "app"))
        self.p._type_blocking = lambda text, delay: {"typed": len(text), "skipped": 0}

        async def steam(self_, text):        # Steam's own keyboard answered
            return True
        self.mod.Plugin._type_via_steam = steam

        async def no_press(tokens, hold):
            return ""
        self.p._press = no_press
        r = asyncio.run(self.p._deliver_text("hello there", force=True))
        return r.get("method"), r.get("status")

    def test_the_app_s_typing_method_is_used(self):
        self.assertEqual(self.way({DOTA: {"name": "Dota 2", "method": "steam"}}, DOTA, "keyboard"),
                         ("steam", "typed"))

    def test_an_app_with_no_method_uses_the_global_method(self):
        self.assertEqual(self.way({DOTA: {"name": "Dota 2"}}, DOTA, "steam"), ("steam", "typed"))

    def test_an_app_with_junk_method_uses_the_global_method(self):
        self.assertEqual(self.way({DOTA: {"name": "Dota 2", "method": "Steam"}}, DOTA, "keyboard"),
                         ("keyboard", "typed"))

    def test_an_app_can_pick_the_virtual_keyboard_when_global_is_steam(self):
        self.assertEqual(self.way({DOTA: {"name": "Dota 2", "method": "keyboard"}}, DOTA, "steam"),
                         ("keyboard", "typed"))


class RumblePerApp(Fixtures):
    """The buzz and its intensity were global only; an app can turn the buzz off for itself,
    set its own step, or follow Global."""

    def buzz(self, overrides, global_on, global_level):
        self.p.rumbler = FakeRumbler()
        self.settings(rumble=global_on, rumble_level=global_level, overrides=overrides)
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        self.p._buzz("start")
        return self.p.rumbler.played

    def test_an_app_that_turns_the_buzz_off_is_not_buzzed(self):
        self.assertEqual(self.buzz({DOTA: {"name": "Dota 2", "rumble": False}}, True, 5), [])

    def test_an_app_with_no_rumble_setting_buzzes_at_the_global_step(self):
        self.assertEqual(self.buzz({DOTA: {"name": "Dota 2"}}, True, 5), [("start", 5)])

    def test_the_app_step_beats_the_global_step(self):
        self.assertEqual(self.buzz({DOTA: {"name": "Dota 2", "rumble_level": 2}}, True, 8),
                         [("start", 2)])

    def test_an_app_can_turn_the_buzz_back_on_when_global_is_off(self):
        self.assertEqual(self.buzz({DOTA: {"name": "Dota 2", "rumble": True}}, False, 5),
                         [("start", 5)])

    def test_the_global_off_switch_stays_off_for_an_app_that_sets_nothing(self):
        self.assertEqual(self.buzz({DOTA: {"name": "Dota 2"}}, False, 5), [])

    def test_junk_in_the_app_rumble_fields_falls_back_to_global(self):
        self.assertEqual(self.buzz({DOTA: {"name": "Dota 2", "rumble": "off", "rumble_level": "2"}},
                                   True, 6), [("start", 6)])


class VoiceDetectorPerApp(Fixtures):
    """The voice detector switch is per model; an app can set it for itself."""

    def detector(self, overrides, running, model, stored_vad):
        self.settings(model=model, vad=stored_vad, overrides=overrides)
        asyncio.run(self.p.set_running_app(running, "app"))
        return self.p._vad_on(model, self.p._app_override())

    def test_the_app_switch_beats_the_global_choice_for_that_model(self):
        self.assertIs(self.detector({DOTA: {"name": "Dota 2", "vad": False}}, DOTA,
                                    "small.en", {"small.en": True}), False)
        self.assertIs(self.detector({DOTA: {"name": "Dota 2", "vad": True}}, DOTA,
                                    "small.en", {"small.en": False}), True)

    def test_an_app_that_sets_nothing_uses_the_global_choice_for_the_model(self):
        self.assertIs(self.detector({DOTA: {"name": "Dota 2"}}, DOTA, "small.en",
                                    {"small.en": False}), False)
        self.assertIs(self.detector({DOTA: {"name": "Dota 2"}}, DOTA, "small.en", None), True)

    def test_another_app_is_not_affected_by_one_app_s_detector_setting(self):
        ov = {DOTA: {"name": "Dota 2", "vad": False}, "1234": {"name": "Adventurer"}}
        self.assertIs(self.detector(ov, "1234", "small.en", {"small.en": True}), True)

    def test_a_parakeet_model_has_no_detector_even_when_the_app_asks_for_one(self):
        self.assertIs(self.detector({DOTA: {"name": "Dota 2", "vad": True}}, DOTA,
                                    "parakeet-tdt-0.6b-v2", {"small.en": True}), False)

    def test_junk_in_the_app_detector_field_falls_back_to_global(self):
        self.assertIs(self.detector({DOTA: {"name": "Dota 2", "vad": "off"}}, DOTA,
                                    "small.en", {"small.en": True}), True)


class KeepingTwoModelsLoaded(Fixtures):
    """Which model goes in which speech server, and what is restarted.

    The plugin's own server start is replaced with a recorder that puts the model in the slot it
    was asked for and marks it running with a real live process, so the plugin's own liveness and
    slot checks are the ones being tested. The real servers and models are run by
    scripts/integration_smoke.py on the Deck.
    """

    def fake_servers(self):
        started = []
        self.fakes = []

        def _start_one(self_, name, slot, vad):
            # The stand mirrors main.py's own _start_one (server_proc/server_model/server_vad for the
            # primary slot, extra_* for the second, {"ok": True} once the port answers) and starts a
            # sleep process instead of a real speech server.
            async def go():
                proc = subprocess.Popen(["sleep", "30"])
                self.fakes.append(proc)
                started.append((name, slot, vad))
                if slot == "primary":
                    self.p.server_proc, self.p.server_model, self.p.server_vad = live(proc), name, vad
                else:
                    self.p.extra_proc, self.p.extra_model, self.p.extra_vad = live(proc), name, vad
                return {"ok": True}
            return go()

        self.mod.Plugin._start_one = _start_one
        return started

    def test_the_app_model_goes_in_the_second_slot_and_the_global_server_is_left_alone(self):
        started = self.fake_servers()
        self.settings(model="small.en", keep_loaded=True,
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda n: True
        self.run_all(self.p.set_running_app(DOTA, "Dota 2"))
        self.run_all(self.p.start_server("small.en"))
        first = self.p.server_proc
        self.run_all(self.p.start_server("large-v3"))
        app = [s for s in started if s[0] == "large-v3"]
        self.assertEqual(app[-1], ("large-v3", "extra", True))
        self.assertIs(self.p.server_proc, first)          # the global model's server was not restarted
        self.assertEqual(self.p.loaded_models(), ["small.en", "large-v3"])

    def test_the_state_shows_both_models_loaded_and_neither_loading(self):
        self.fake_servers()
        self.settings(model="small.en", keep_loaded=True,
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda n: True
        self.run_all(self.p.set_running_app(DOTA, "Dota 2"))   # includes the background model load
        st = asyncio.run(self.p.get_state())
        # what the panel needs to say: which models are resident, is this app's model still
        # coming up, and is an app's own model in force
        self.assertEqual(st["loaded_models"], ["small.en", "large-v3"])
        self.assertEqual(st["loading_model"], False)   # nothing is being waited on for a dictation
        out = asyncio.run(self.p.get_overrides())
        self.assertEqual(out["model"], "large-v3")
        self.assertEqual(out["model_loaded"], True)
        self.assertEqual(out["model_is_global"], False)
        self.assertEqual(out["keep_loaded"], True)

    def test_going_back_to_the_global_model_reuses_the_running_server(self):
        started = self.fake_servers()
        self.settings(model="small.en", keep_loaded=True,
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda n: True
        asyncio.run(self.p.start_server("small.en"))
        asyncio.run(self.p.start_server("large-v3"))
        n = len(started)
        asyncio.run(self.p.set_running_app("999", "Other game"))    # an app with no model of its own
        r = asyncio.run(self.p.start_server("small.en"))
        self.assertEqual((r.get("ok"), len(started)), (True, n))     # nothing restarted
        self.assertEqual(self.p._slot_of("small.en"), "primary")
        self.assertEqual(self.p._slot_of("large-v3"), "extra")

    def test_the_second_slot_is_reused_when_a_third_app_wants_a_third_model(self):
        started = self.fake_servers()
        self.settings(model="small.en", keep_loaded=True,
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"},
                                 "1234": {"name": "Adventurer", "model": "medium.en"}})
        self.p._model_ok = lambda n: True
        asyncio.run(self.p.start_server("small.en"))
        asyncio.run(self.p.start_server("large-v3"))
        first_extra = self.p.extra_proc
        asyncio.run(self.p.start_server("medium.en"))
        self.assertEqual(started[-1], ("medium.en", "extra", True))
        self.assertIsNot(self.p.extra_proc, first_extra)   # the second slot was restarted
        self.assertEqual(self.p._slot_of("large-v3"), "")  # it is no longer loaded
        self.assertEqual(self.p._slot_of("small.en"), "primary")

    def test_with_the_switch_off_there_is_one_server_as_before(self):
        started = self.fake_servers()
        self.settings(model="small.en", keep_loaded=False,
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda n: True
        asyncio.run(self.p.start_server("small.en"))
        asyncio.run(self.p.start_server("large-v3"))
        self.assertEqual(started[-1], ("large-v3", "primary", True))
        self.assertEqual(self.p.loaded_models(), ["large-v3"])
        self.assertIsNone(self.p.extra_proc)

    def test_a_voice_detector_setting_the_server_was_not_started_with_restarts_that_slot(self):
        started = self.fake_servers()
        self.settings(model="small.en", keep_loaded=True, vad={"large-v3": True},
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3", "vad": False}})
        self.p._model_ok = lambda n: True
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        asyncio.run(self.p.start_server("large-v3"))
        mine = [s for s in started if s[0] == "large-v3"]
        self.assertEqual(mine[-1], ("large-v3", "extra", False))
        # the same model with the detector back on: the slot comes up again with it
        self.settings(overrides={DOTA: {"name": "Dota 2", "model": "large-v3", "vad": True}})
        asyncio.run(self.p.start_server("large-v3"))
        mine = [s for s in started if s[0] == "large-v3"]
        self.assertEqual(mine[-1], ("large-v3", "extra", True))

    def test_turning_the_switch_off_frees_the_second_server(self):
        self.fake_servers()
        self.settings(model="small.en", keep_loaded=True)
        self.p._model_ok = lambda n: True
        asyncio.run(self.p.start_server("small.en"))
        asyncio.run(self.p.start_server("large-v3"))
        self.assertIsNotNone(self.p.extra_proc)
        r = asyncio.run(self.p.set_keep_loaded(False))
        self.assertEqual((r["ok"], self.p.extra_proc, self.p.loaded_models()),
                         (True, None, ["small.en"]))

    def test_the_panel_is_told_which_models_are_loaded(self):
        self.fake_servers()
        self.settings(model="small.en", keep_loaded=True,
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        self.p._model_ok = lambda n: True
        asyncio.run(self.p.start_server("small.en"))
        asyncio.run(self.p.start_server("large-v3"))
        out = asyncio.run(self.p.get_overrides())
        self.assertEqual(out["loaded_models"], ["small.en", "large-v3"])
        self.assertEqual(out["keep_loaded"], True)
        by = {o["appid"]: o for o in out["overrides"]}
        self.assertEqual(by[DOTA]["model_loaded"], True)


class HiddenApps(Fixtures):
    def test_hiding_an_app_stores_it_and_the_panel_sees_it(self):
        self.settings(overrides={DOTA: {"name": "Dota 2"}})
        r = asyncio.run(self.p.set_app_hidden(DOTA, True))
        self.assertEqual(r["ok"], True)
        self.assertEqual(r["hidden"], [DOTA])
        by = {o["appid"]: o for o in r["overrides"]}
        self.assertEqual(by[DOTA]["hidden"], True)
        r2 = asyncio.run(self.p.set_app_hidden(DOTA, False))
        self.assertEqual(r2["hidden"], [])

    def test_a_hidden_app_is_not_reported_as_having_no_override(self):
        # hiding is a panel preference: the override keeps applying to the app
        self.settings(activator=GLOBAL_ACT,
                      overrides={DOTA: {"name": "Dota 2", "activator": APP_ACT, "speed": "slow"}})
        asyncio.run(self.p.set_app_hidden(DOTA, True))
        asyncio.run(self.p.set_running_app(DOTA, "Dota 2"))
        out = asyncio.run(self.p.get_overrides())
        self.assertEqual(out["running"]["has_override"], True)
        self.assertEqual(self.p.hub.activator, ubermoc_input.valid_tokens(APP_ACT))
        self.assertEqual({o["appid"]: o for o in out["overrides"]}[DOTA]["speed"], "slow")

    def test_the_show_hidden_switch_is_stored_and_read_back(self):
        r = asyncio.run(self.p.set_show_hidden(True))
        self.assertEqual(r["show_hidden"], True)
        self.assertEqual(self.p._load_settings().get("show_hidden"), True)
        r = asyncio.run(self.p.set_show_hidden(False))
        self.assertEqual(r["show_hidden"], False)

    def test_hiding_needs_a_real_appid_and_the_list_is_capped(self):
        r = asyncio.run(self.p.set_app_hidden("abc", True))
        self.assertEqual((r["ok"], r["error"]), (False, "no such application"))
        many = {str(i): True for i in range(ubermoc_typing.MAX_OVERRIDES)}
        self.settings(hidden=many)
        r = asyncio.run(self.p.set_app_hidden("9999", True))
        self.assertEqual(r["ok"], False)
        self.assertIn("can be hidden", r["error"])

    def test_an_old_settings_file_has_no_hidden_apps(self):
        out = asyncio.run(self.p.get_overrides())
        self.assertEqual((out["hidden"], out["show_hidden"], out["keep_loaded"]), ([], False, False))


class PanelFieldsForTheNewOptions(Fixtures):
    """The panel shows one row per global option, so each new field must be readable and settable
    through the one setter the panel uses, and junk must never be stored."""

    def set(self, field, value):
        r = asyncio.run(self.p.set_override_field(DOTA, field, value))
        return r, {o["appid"]: o for o in r["overrides"]}[DOTA]

    def setUp(self):
        super().setUp()
        self.settings(overrides={DOTA: {"name": "Dota 2"}})

    def test_every_row_the_panel_shows_can_be_set_and_cleared(self):
        # what the panel sees after clearing: "" / None mean "this app follows Global again",
        # and for spoken symbols the global value is shown
        cleared = {"speed": "", "method": "", "symbols": "words",
                   "rumble": None, "rumble_level": None, "vad": None}
        cases = [("method", "steam", "method"), ("symbols", "symbols", "symbols"),
                 ("rumble", False, "rumble"), ("rumble_level", 7, "rumble_level"),
                 ("vad", False, "vad"), ("speed", "slow", "speed")]
        for field, value, key in cases:
            r, ov = self.set(field, value)
            self.assertEqual((r["ok"], ov[key]), (True, value), field)
            r, ov = self.set(field, "")
            self.assertEqual((r["ok"], ov[key]), (True, cleared[key]), field)

    def test_junk_is_refused_and_nothing_is_stored(self):
        for value in ("Steam", "loud", 11, "off"):
            r, ov = self.set("method", value)
            self.assertEqual(ov["method"], "")
        r, ov = self.set("rumble_level", 11)
        self.assertEqual(ov["rumble_level"], None)
        r, ov = self.set("rumble", "false")
        self.assertEqual(ov["rumble"], None)

    def test_an_unknown_field_is_reported_as_a_failure(self):
        r, ov = self.set("volume", "loud")
        self.assertEqual((r["ok"], r["error"]), (False, "cannot change volume"))

    def test_a_model_change_stops_both_servers_so_the_next_dictation_loads_the_new_model(self):
        self.settings(model="small.en")
        proc = subprocess.Popen(["sleep", "30"])
        self.p.server_proc = live(proc)
        self.p.server_model = "small.en"
        proc2 = subprocess.Popen(["sleep", "30"])
        self.p.extra_proc = live(proc2)
        self.p.extra_model = "large-v3"
        asyncio.run(self.p.set_override_field(DOTA, "model", "large-v3"))
        self.assertIsNone(self.p.server_proc)
        self.assertIsNone(self.p.extra_proc)

    def test_a_voice_detector_change_stops_the_server_running_that_model_only(self):
        self.settings(model="small.en", keep_loaded=True,
                      overrides={DOTA: {"name": "Dota 2", "model": "large-v3"}})
        proc = subprocess.Popen(["sleep", "30"])
        self.p.server_proc = live(proc)
        self.p.server_model = "small.en"
        proc2 = subprocess.Popen(["sleep", "30"])
        self.p.extra_proc = live(proc2)
        self.p.extra_model = "large-v3"
        asyncio.run(self.p.set_override_field(DOTA, "vad", False))
        self.assertIsNone(self.p.extra_proc)      # the server that runs the app's model
        self.assertIsNotNone(self.p.server_proc)  # the global model's server is left running
        proc.kill(); proc2.kill()


if __name__ == "__main__":
    unittest.main()
