# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Tests for py_modules/ubermoc_typing.py (no real /dev/uinput needed).
Run: python3 -m unittest discover -s tests -v"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
import ubermoc_input as ui  # noqa: E402
import ubermoc_typing as ut  # noqa: E402
from ubermoc_codes import KEY_NAMES  # noqa: E402

# What a US QWERTY keyboard produces: code -> (plain, shifted). Built independently of
# the plugin's own table so the round-trip test is a real check.
US = {}
for row in ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"):
    for ch in row:
        US[{v: k for k, v in KEY_NAMES.items()}[ch]] = (ch.lower(), ch)
for ch, shifted in zip("1234567890", "!@#$%^&*()"):
    US[{v: k for k, v in KEY_NAMES.items()}[ch]] = (ch, shifted)
for name, plain, shifted in (("MINUS", "-", "_"), ("EQUAL", "=", "+"), ("LEFTBRACE", "[", "{"),
                             ("RIGHTBRACE", "]", "}"), ("SEMICOLON", ";", ":"), ("APOSTROPHE", "'", '"'),
                             ("GRAVE", "`", "~"), ("BACKSLASH", "\\", "|"), ("COMMA", ",", "<"),
                             ("DOT", ".", ">"), ("SLASH", "/", "?"), ("SPACE", " ", " ")):
    US[{v: k for k, v in KEY_NAMES.items()}[name]] = (plain, shifted)


def decode(raw: bytes) -> str:
    """Replay recorded input events through a US keyboard and return what it would type."""
    out, shift, down = [], False, set()
    for off in range(0, len(raw), ut.EVENT.size):
        _s, _u, etype, code, value = ut.EVENT.unpack_from(raw, off)
        if etype != ut.EV_KEY:
            continue
        if code == ut.KEY_LEFTSHIFT:
            shift = bool(value)
        elif value == 1:
            down.add(code)
            plain, shifted = US[code]
            out.append(shifted if shift else plain)
        elif value == 0:
            down.discard(code)
    assert not down, "a key was left pressed"
    assert not shift, "shift was left pressed"
    return "".join(out)


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "uinput")
        open(self.path, "wb").close()
        self.ioctls, self.sleeps = [], []
        self.kb = ut.VirtualKeyboard(self.path, ioctl=lambda fd, req, arg=0: self.ioctls.append((req, arg)),
                                     sleep=self.sleeps.append)

    def written(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()

    def close(self):
        self.kb.close()
        self.tmp.cleanup()


class Typing(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def test_every_printable_ascii_character_round_trips(self):
        text = "".join(chr(c) for c in range(32, 127))
        for ch in text:
            self.assertIn(ch, ut.CHAR_MAP, ch)
        self.e.kb.type_text(text)
        self.assertEqual(decode(self.e.written()), text)

    def test_sentence_round_trip(self):
        text = "Hey Tom, are you free at 7:30? I'll bring the \"good\" stuff (50% off) - see you!"
        r = self.e.kb.type_text(text)
        self.assertEqual(decode(self.e.written()), text)
        self.assertEqual(r, {"typed": len(text), "skipped": 0})

    def test_non_ascii_is_skipped_and_counted(self):
        r = self.e.kb.type_text("café ok")
        self.assertEqual(decode(self.e.written()), "caf ok")
        self.assertEqual(r, {"typed": 6, "skipped": 1})

    def test_shift_is_held_across_consecutive_capitals_and_released_at_the_end(self):
        self.e.kb.type_text("ABc!")
        raw = self.e.written()
        events = [(c, v) for _s, _u, t, c, v in (ut.EVENT.unpack_from(raw, o) for o in range(0, len(raw), ut.EVENT.size)) if t == ut.EV_KEY]
        shift_events = [(c, v) for c, v in events if c == ut.KEY_LEFTSHIFT]
        self.assertEqual(shift_events, [(42, 1), (42, 0), (42, 1), (42, 0)])   # A B | c | ! |
        self.assertEqual(decode(raw), "ABc!")

    def test_text_ending_with_a_capital_still_releases_shift(self):
        self.e.kb.type_text("OK")
        self.assertEqual(decode(self.e.written()), "OK")                         # decode asserts shift released

    def test_device_is_created_once_with_the_right_calls(self):
        self.e.kb.type_text("a")
        self.e.kb.type_text("b")
        reqs = [r for r, _ in self.e.ioctls]
        self.assertEqual(reqs.count(ut.UI_DEV_CREATE), 1)
        self.assertEqual(reqs[0], ut.UI_SET_EVBIT)
        self.assertEqual(self.e.ioctls[0][1], ut.EV_KEY)
        keybits = [a for r, a in self.e.ioctls if r == ut.UI_SET_KEYBIT]
        self.assertEqual(keybits, list(range(1, 256)))
        setup = next(a for r, a in self.e.ioctls if r == ut.UI_DEV_SETUP)
        self.assertEqual(len(setup), 92)
        self.assertEqual(setup[8:88].rstrip(b"\0"), b"Ubermoc Speech Keyboard")
        self.assertIn(ut.CREATE_SETTLE_S, self.e.sleeps)                          # waits for the desktop to notice it

    def test_close_destroys_the_device(self):
        self.e.kb.type_text("a")
        self.e.kb.close()
        self.assertEqual(self.e.ioctls[-1][0], ut.UI_DEV_DESTROY)
        self.assertFalse(self.e.kb.created)

    def test_pacing(self):
        self.e.kb.type_text("ab", delay_s=0.01)
        self.assertEqual([s for s in self.e.sleeps if s != ut.CREATE_SETTLE_S], [0.005] * 4)

    def test_write_failure_raises_and_recreates_next_time(self):
        self.e.kb.open()
        os.close(self.e.kb._fd)                                                   # simulate the device vanishing
        with self.assertRaises(ut.KeyboardError):
            self.e.kb.type_text("hello")
        self.assertFalse(self.e.kb.created)

    def test_missing_device_reports_a_reason(self):
        kb = ut.VirtualKeyboard(os.path.join(self.e.tmp.name, "nope"))
        orig = ut.subprocess.run
        tried = []
        ut.subprocess.run = lambda *a, **k: tried.append(a[0])
        try:
            ok, reason = kb.check()
        finally:
            ut.subprocess.run = orig
        self.assertFalse(ok)
        self.assertIn("missing", reason)
        self.assertEqual(tried, [["modprobe", "uinput"]])                         # it tries to load the module first
        with self.assertRaises(ut.KeyboardError):
            ut.subprocess.run = lambda *a, **k: None
            try:
                kb.open()
            finally:
                ut.subprocess.run = orig

    def test_own_device_name_matches_what_the_hub_ignores(self):
        self.assertEqual(ut.DEVICE_NAME, ui.VIRTUAL_KEYBOARD_NAME)


def key_events(raw: bytes):
    """[(code, value)] of the key events in a recording."""
    out = []
    for off in range(0, len(raw), ut.EVENT.size):
        _s, _u, etype, code, value = ut.EVENT.unpack_from(raw, off)
        if etype == ut.EV_KEY:
            out.append((code, value))
    return out


class LoggedDevice:
    """Mixin: record every event with the device name, in order, in a shared log."""
    log = None
    label = ""

    def _emit(self, code, value):
        self.log.append((self.label, "key", code, value))

    def _emit_abs(self, code, value):
        self.log.append((self.label, "abs", code, value))


def make_presser():
    log, sleeps, ioctls = [], [], {"keyboard": [], "mouse": [], "pad": []}

    def fake(cls, label):
        class D(LoggedDevice, cls):
            pass
        D.label = label
        d = D("/dev/null", ioctl=lambda fd, req, arg=0: ioctls[label].append((req, arg)), sleep=lambda s: None)
        d.log = log
        d.check = lambda: (True, "")
        d.open = lambda: setattr(d, "_fd", 1) if d._fd is None else None
        d.close = lambda: setattr(d, "_fd", None)
        return d
    kb, mouse, pad = fake(ut.VirtualKeyboard, "keyboard"), fake(ut.VirtualMouse, "mouse"), fake(ut.VirtualGamepad, "pad")
    return ut.Presser(kb, mouse, pad, sleep=sleeps.append), log, sleeps, (kb, mouse, pad)


class PressingInputs(unittest.TestCase):
    def test_a_keyboard_combination_is_pressed_the_documented_way(self):
        pr, log, _s, _d = make_presser()
        pr.press(["key:59", "key:56", "key:29"])
        # ydotool: 29:1 56:1 59:1 59:0 56:0 29:0 (Ctrl+Alt+F1)
        self.assertEqual([(c, v) for _l, _k, c, v in log], [(29, 1), (56, 1), (59, 1), (59, 0), (56, 0), (29, 0)])

    def test_a_mouse_click(self):
        pr, log, _s, _d = make_presser()
        pr.press(["mouse:272"])
        self.assertEqual(log, [("mouse", "key", 272, 1), ("mouse", "key", 272, 0)])

    def test_a_controller_button(self):
        pr, log, _s, _d = make_presser()
        pr.press(["pad:A"])
        self.assertEqual(log, [("pad", "key", 0x130, 1), ("pad", "key", 0x130, 0)])

    def test_triggers_are_axes_like_an_xbox_360_pad(self):
        pr, log, _s, _d = make_presser()
        pr.press(["pad:L2"])
        self.assertEqual(log, [("pad", "abs", ut.ABS_Z, 255), ("pad", "abs", ut.ABS_Z, 0)])
        log.clear()
        pr.press(["pad:R2"])
        self.assertEqual(log, [("pad", "abs", ut.ABS_RZ, 255), ("pad", "abs", ut.ABS_RZ, 0)])

    def test_steam_deck_back_buttons_use_the_kernel_grip_codes(self):
        pr, log, _s, _d = make_presser()
        pr.press(["pad:R4", "pad:R5", "pad:L4", "pad:L5"])
        pressed = {c for _l, _k, c, v in log if v == 1}
        self.assertEqual(pressed, {0x224, 0x225, 0x226, 0x227})      # BTN_GRIPL/R/L2/R2 (gamepad.rst)

    def test_mixed_devices_in_one_combination_in_order_and_released_in_reverse(self):
        pr, log, _s, _d = make_presser()
        pr.press(["pad:A", "mouse:272", "key:20", "key:29"])
        self.assertEqual([(l, c, v) for l, _k, c, v in log], [
            ("keyboard", 29, 1), ("keyboard", 20, 1), ("mouse", 272, 1), ("pad", 0x130, 1),
            ("pad", 0x130, 0), ("mouse", 272, 0), ("keyboard", 20, 0), ("keyboard", 29, 0)])

    def test_held_long_enough_with_a_head_start_for_the_first_input(self):
        pr, _log, sleeps, _d = make_presser()
        pr.press(["key:29", "key:20"], hold_s=0.12)
        self.assertIn(0.12, sleeps)
        self.assertEqual(sleeps[0], ut.MOD_LEAD_S)

    def test_unknown_controller_buttons_are_skipped_and_reported(self):
        pr, log, _s, devs = make_presser()
        r = pr.press(["pad:TRIGGER_HAPPY1", "pad:A"])
        self.assertEqual(r, {"pressed": 1, "skipped": ["TRIGGER_HAPPY1"]})
        r = pr.press(["pad:TRIGGER_HAPPY1"])
        self.assertEqual(r, {"pressed": 0, "skipped": ["TRIGGER_HAPPY1"]})

    def test_only_the_devices_that_are_needed_get_created(self):
        pr, _log, _s, (kb, mouse, pad) = make_presser()
        pr.prepare(["key:28"])
        self.assertEqual((kb.created, mouse.created, pad.created), (True, False, False))
        pr.prepare(["mouse:272", "pad:A"])
        self.assertEqual((mouse.created, pad.created), (True, True))

    def test_nothing_is_left_pressed_if_a_write_fails(self):
        pr, log, _s, (kb, mouse, pad) = make_presser()
        calls = []
        def flaky(code, value, orig=pad._emit):
            calls.append(value)
            if value == 1:
                raise OSError("gone")
            orig(code, value)
        pad._emit = flaky
        with self.assertRaises(ut.KeyboardError):
            pr.press(["key:29", "pad:A"])
        downs = [(l, c) for l, _k, c, v in log if v == 1]
        ups = [(l, c) for l, _k, c, v in log if v == 0]
        self.assertEqual(downs, [("keyboard", 29)])
        self.assertEqual(ups, [("keyboard", 29)])                      # the key that went down was released


class DeviceSetup(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def _setup(self, cls):
        calls = []
        d = cls(self.e.path, ioctl=lambda fd, req, arg=0: calls.append((req, arg)), sleep=lambda s: None)
        d.open()
        d.close()
        return calls

    def test_mouse_declares_the_standard_buttons_and_relative_axes(self):
        calls = self._setup(ut.VirtualMouse)
        self.assertEqual([a for r, a in calls if r == ut.UI_SET_KEYBIT], list(range(0x110, 0x118)))
        self.assertEqual([a for r, a in calls if r == ut.UI_SET_RELBIT], [0, 1, 8])
        self.assertIn((ut.UI_SET_EVBIT, ut.EV_REL), calls)
        setup = next(a for r, a in calls if r == ut.UI_DEV_SETUP)
        self.assertEqual(setup[8:88].rstrip(b"\0"), b"Ubermoc Speech Mouse")

    def test_gamepad_looks_like_an_xbox_360_pad(self):
        calls = self._setup(ut.VirtualGamepad)
        setup = next(a for r, a in calls if r == ut.UI_DEV_SETUP)
        bus, vendor, product, version, name, _ff = ut.SETUP.unpack(setup)
        self.assertEqual((vendor, product, version), (0x045E, 0x028E, 0x0110))
        self.assertEqual(name.rstrip(b"\0"), b"Ubermoc Speech Gamepad")
        axes = [a for r, a in calls if r == ut.UI_ABS_SETUP]
        self.assertEqual([ut.ABS_SETUP.unpack(a)[0] for a in axes], [ut.ABS_Z, ut.ABS_RZ])
        for a in axes:
            _code, _pad, _value, lo, hi, _fuzz, _flat, _res = ut.ABS_SETUP.unpack(a)
            self.assertEqual((lo, hi), (0, 255))

    def test_gamepad_buttons_keep_sdl_s_numbering_the_same_as_a_real_xbox_360_pad(self):
        # SDL (src/joystick/linux/SDL_sysjoystick.c) numbers buttons by ascending code,
        # starting at BTN_JOYSTICK (0x120), then the codes below it. A button numbered
        # below BTN_SOUTH would shift every game's controller layout.
        declared = sorted(ut.VirtualGamepad.KEYS)
        sdl_order = [c for c in declared if c >= 0x120] + [c for c in declared if c < 0x120]
        index = {code: i for i, code in enumerate(sdl_order)}
        xbox_360 = [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x13A, 0x13B, 0x13C, 0x13D, 0x13E]
        self.assertEqual([index[c] for c in xbox_360], list(range(11)))   # A B Y X LB RB Back Start Guide L3 R3
        self.assertNotIn(0x138, declared)                                 # BTN_TL2 would shift Back/Start/Guide
        self.assertNotIn(0x139, declared)
        self.assertTrue(all(c >= 0x130 for c in declared))

    def test_all_plugin_devices_are_ignored_by_the_input_reader(self):
        self.assertEqual(ui.VIRTUAL_DEVICE_NAMES, {ut.VirtualKeyboard.NAME, ut.VirtualMouse.NAME, ut.VirtualGamepad.NAME})


class KeyActions(unittest.TestCase):
    def test_actions(self):
        self.assertEqual(ut.action_tokens({"mode": "enter"}), ["key:28"])
        self.assertEqual(ut.action_tokens({"mode": "none"}), [])
        self.assertEqual(ut.action_tokens({"mode": "custom", "keys": ["key:20", "key:42", "key:29"]}),
                         ["key:29", "key:42", "key:20"])
        self.assertEqual(ut.action_label({"mode": "none"}), "None")
        self.assertEqual(ut.action_label({"mode": "enter"}), "Enter")
        self.assertEqual(ut.action_label({"mode": "custom", "keys": ["key:20", "key:29"]}), "Left Ctrl + T")
        self.assertEqual(ut.action_label({"mode": "custom", "keys": []}), "Not set")

    def test_any_kind_of_input_can_be_the_action(self):
        a = ut.normalize_action({"mode": "custom", "keys": ["pad:A", "mouse:272", "key:29"]})
        self.assertEqual(sorted(a["keys"]), ["key:29", "mouse:272", "pad:A"])
        self.assertEqual(ut.action_label(a), "Left Ctrl + Left click + A")
        self.assertEqual(ut.action_tokens(a), ["key:29", "mouse:272", "pad:A"])

    def test_defaults_are_enter_before_and_after(self):
        self.assertEqual(ut.normalize_action(None), {"mode": "enter", "keys": []})
        self.assertEqual(ut.normalize_action("junk"), {"mode": "enter", "keys": []})
        self.assertEqual(ut.normalize_action({"mode": "bogus"}), {"mode": "enter", "keys": []})

    def test_junk_tokens_dropped_and_count_capped(self):
        self.assertEqual(ut.normalize_action({"mode": "custom", "keys": ["bogus", "key:x", "key:29"]})["keys"], ["key:29"])
        many = ["key:%d" % c for c in (29, 42, 56, 125, 20)]
        self.assertEqual(len(ut.normalize_action({"mode": "custom", "keys": many})["keys"]), 4)

    def test_inputs_are_dropped_unless_the_mode_is_custom(self):
        self.assertEqual(ut.normalize_action({"mode": "enter", "keys": ["key:20"]})["keys"], [])

    def test_warnings(self):
        self.assertEqual(ut.action_warnings({"mode": "custom", "keys": ["key:29", "mouse:272", "pad:A"]}), [])
        w = ut.action_warnings({"mode": "custom", "keys": ["pad:R4"]})
        self.assertEqual(len(w), 1)
        self.assertIn("R4", w[0])
        w = ut.action_warnings({"mode": "custom", "keys": ["pad:TRIGGER_HAPPY1"]})
        self.assertIn("cannot be pressed", w[0])

    def test_every_controller_name_the_activator_can_record_is_either_pressable_or_reported(self):
        recordable = set(ui.DECK_BUTTONS.values()) | set(ui.SC_BUTTONS.values()) | set(ui.TRITON_BUTTONS.values())
        pressable = set(ut.PAD_OUTPUTS)
        # buttons with no equivalent on a virtual pad are the touch/analog details only
        self.assertEqual(recordable - pressable, set())

    def test_timing_table_is_sane(self):
        for name, (delay, hold, before, after) in ut.TIMING.items():
            self.assertGreaterEqual(hold, 0.03, name)
            self.assertGreater(hold, delay * 3, name)
        self.assertLess(ut.TIMING["fast"][1], ut.TIMING["normal"][1])
        self.assertLess(ut.TIMING["normal"][1], ut.TIMING["slow"][1])


class Overrides(unittest.TestCase):
    def test_a_valid_override_is_kept(self):
        raw = {"570": {"name": "Dota 2", "kind": "steam",
                       "before": {"mode": "none"}, "after": {"mode": "custom", "keys": ["pad:A", "key:29"]}}}
        out = ut.normalize_overrides(raw)
        self.assertEqual(out["570"]["name"], "Dota 2")
        self.assertEqual(out["570"]["before"], {"mode": "none", "keys": []})
        self.assertEqual(sorted(out["570"]["after"]["keys"]), ["key:29", "pad:A"])

    def test_junk_is_dropped(self):
        raw = {"abc": {"name": "x"}, "570": "nope", "1": {"name": "  "}, "2": {"name": "Ok"}, 3: {"name": "int key"}}
        self.assertEqual(list(ut.normalize_overrides(raw)), ["2"])
        self.assertEqual(ut.normalize_overrides(None), {})
        self.assertEqual(ut.normalize_overrides([1, 2]), {})

    def test_missing_actions_default_to_enter(self):
        out = ut.normalize_overrides({"5": {"name": "Game"}})
        self.assertEqual(out["5"]["before"], {"mode": "enter", "keys": []})
        self.assertEqual(out["5"]["after"], {"mode": "enter", "keys": []})
        self.assertEqual(out["5"]["kind"], "steam")

    def test_shortcut_kind(self):
        self.assertEqual(ut.normalize_overrides({"5": {"name": "App", "kind": "shortcut"}})["5"]["kind"], "shortcut")
        self.assertEqual(ut.normalize_overrides({"5": {"name": "App", "kind": "weird"}})["5"]["kind"], "steam")

    def test_the_count_is_capped(self):
        raw = {str(i): {"name": f"g{i}"} for i in range(ut.MAX_OVERRIDES + 20)}
        self.assertEqual(len(ut.normalize_overrides(raw)), ut.MAX_OVERRIDES)

    def test_running_application_with_an_override_uses_it(self):
        s = {"before": {"mode": "enter"}, "after": {"mode": "enter"},
             "overrides": {"570": {"name": "Dota 2", "before": {"mode": "none"}, "after": {"mode": "custom", "keys": ["key:28"]}}}}
        before, after, name = ut.actions_for(s, "570")
        self.assertEqual((before["mode"], after["mode"], name), ("none", "custom", "Dota 2"))

    def test_everything_else_uses_the_global_options(self):
        s = {"before": {"mode": "none"}, "after": {"mode": "enter"},
             "overrides": {"570": {"name": "Dota 2", "before": {"mode": "enter"}}}}
        for appid in ("999", "", None):
            before, after, name = ut.actions_for(s, appid)
            self.assertEqual((before["mode"], after["mode"], name), ("none", "enter", None))

    def test_with_nothing_stored_the_defaults_are_enter_and_enter(self):
        before, after, name = ut.actions_for({}, "570")
        self.assertEqual((before["mode"], after["mode"], name), ("enter", "enter", None))


class Cleaning(unittest.TestCase):
    def test_removes_sound_annotations(self):
        self.assertEqual(ut.clean_for_typing(" [BLANK_AUDIO] "), "")
        self.assertEqual(ut.clean_for_typing("hello [Music] there"), "hello there")
        self.assertEqual(ut.clean_for_typing("(applause)"), "")
        self.assertEqual(ut.clean_for_typing("call me (maybe)"), "call me (maybe)")   # real speech is kept

    def test_typographic_characters_become_plain(self):
        self.assertEqual(ut.clean_for_typing("It’s “fine” – really…"), "It's \"fine\" - really...")

    def test_line_breaks_never_press_enter(self):
        self.assertEqual(ut.clean_for_typing("one\ntwo\r\nthree\tfour"), "one two three four")

    def test_trims_and_collapses_spaces(self):
        self.assertEqual(ut.clean_for_typing("  a   b  "), "a b")


class HubIgnoresOwnKeyboard(unittest.TestCase):
    def test_discover_skips_the_virtual_keyboard(self):
        sample = """I: Bus=0003 Vendor=1209 Product=5e01 Version=0001
N: Name="Ubermoc Speech Keyboard"
H: Handlers=sysrq kbd event9 leds
B: EV=120013
B: KEY={keys}

I: Bus=0003 Vendor=046d Product=c31c Version=0110
N: Name="Logitech USB Keyboard"
H: Handlers=sysrq kbd event3 leds
B: EV=120013
B: KEY={keys}
""".format(keys="1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe")
        with tempfile.TemporaryDirectory() as root:
            proc = os.path.join(root, "devices")
            with open(proc, "w") as f:
                f.write(sample)
            loop = asyncio.new_event_loop()
            hub = ui.InputHub(loop, lambda a: None, proc_path=proc, dev_root=root, sys_root=root)
            found = hub._discover()
            loop.close()
        self.assertEqual(list(found), ["input/event3"])


if __name__ == "__main__":
    unittest.main()


class SpokenSymbols(unittest.TestCase):
    def test_conversions(self):
        f = ut.spoken_symbols
        self.assertEqual(f("C backslash users backslash Documents."), "C\\users\\Documents.")
        self.assertEqual(f("Backslash."), "\\")
        self.assertEqual(f("Back slash n"), "\\n")
        self.assertEqual(f("email bob at sign example.com"), "email bob@example.com")
        self.assertEqual(f("call foo open parenthesis x close parenthesis semicolon"), "call foo (x);")
        self.assertEqual(f("a plus sign b equals sign c"), "a + b = c")
        self.assertEqual(f("Hash tag winning, Dollar sign 5"), "# winning, $ 5")

    def test_ordinary_words_are_left_alone(self):
        for t in ("this is a pipe dream and a dot", "Meet me at the period piece, comma", "a star is born"):
            self.assertEqual(ut.spoken_symbols(t), t)

    def test_every_symbol_can_be_typed(self):
        for _n, ch, _h in ut.SPOKEN_SYMBOLS:
            self.assertIn(ch, ut.CHAR_MAP)


class OverrideSymbols(unittest.TestCase):
    def test_follows_global_unless_overridden(self):
        ov = {"570": {"name": "Dota 2", "kind": "steam", "before": {"mode": "enter", "keys": []},
                      "after": {"mode": "enter", "keys": []}, "symbols": "symbols"},
              "730": {"name": "CS", "kind": "steam", "before": {"mode": "enter", "keys": []},
                      "after": {"mode": "enter", "keys": []}}}
        s = {"overrides": ov, "type_symbols": "words"}
        self.assertEqual(ut.symbols_for(s, "570"), "symbols")
        self.assertEqual(ut.symbols_for(s, "730"), "words")       # no choice stored: follows global
        self.assertEqual(ut.symbols_for(s, "999"), "words")
        s["type_symbols"] = "symbols"
        self.assertEqual(ut.symbols_for(s, "730"), "symbols")
        ov["570"]["symbols"] = "words"
        self.assertEqual(ut.symbols_for(s, "570"), "words")
        ov["570"]["symbols"] = "junk"
        self.assertEqual(ut.symbols_for(s, "570"), "symbols")      # invalid value ignored
