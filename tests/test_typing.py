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
