"""Tests for py_modules/ubermoc_input.py using synthetic devices (pipes).

Run: python3 -m unittest discover -s tests -v
"""
import asyncio
import os
import struct
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
import ubermoc_input as ui  # noqa: E402


def ev(etype, code, value):
    return ui.EVENT.pack(0, 0, etype, code, value)


def deck_report(*bits):
    mask = 0
    for b in bits:
        mask |= 1 << b
    body = struct.pack("<HBB", 1, 9, 64) + struct.pack("<I", 7) + struct.pack("<Q", mask)
    return body + bytes(64 - len(body))


def bitmap(*bits):
    """Format bits like the kernel's `B: KEY=` line (64-bit words, MSB first)."""
    nwords = (max(bits) // 64) + 1
    words = [0] * nwords
    for b in bits:
        words[b // 64] |= 1 << (b % 64)
    return " ".join(f"{w:x}" for w in reversed(words))


class Labels(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(ui.describe(["key:67", "key:29"]), "Left Ctrl + F9")
        self.assertEqual(ui.describe(["pad:R5", "pad:R4"]), "R4 + R5")
        self.assertEqual(ui.describe(["mouse:275"]), "Mouse side button")
        self.assertEqual(ui.describe(["key:30", "mouse:272", "pad:A"]), "A + Left click + A")

    def test_valid_tokens_drops_junk(self):
        self.assertEqual(ui.valid_tokens(["key:30", "bogus", 5, "key:x", "pad:", "key:30", "mouse:272"]),
                         ["key:30", "mouse:272"])
        self.assertEqual(ui.valid_tokens("nope"), [])

    def test_warnings(self):
        self.assertTrue(ui.warnings_for(["mouse:272"]))
        self.assertTrue(ui.warnings_for(["key:29"]))
        self.assertFalse(ui.warnings_for(["key:29", "key:67"]))
        self.assertFalse(ui.warnings_for(["mouse:275"]))


class DeckReport(unittest.TestCase):
    def test_buttons(self):
        held = ui.parse_deck_report(deck_report(42, 16))      # R4 + R5
        self.assertEqual(held, {"pad:R4", "pad:R5"})
        self.assertEqual(ui.parse_deck_report(deck_report()), frozenset())

    def test_rejects_other_reports(self):
        good = deck_report(7)
        self.assertIsNotNone(ui.parse_deck_report(good))
        for bad in (good[:63], good + b"\0", b"\x02" + good[1:], good[:1] + b"\x01" + good[2:],
                    good[:2] + b"\x0a" + good[3:], good[:3] + b"\x20" + good[4:]):
            self.assertIsNone(ui.parse_deck_report(bad))

    def test_table_matches_kernel_and_sdl(self):
        # hid-steam.c steam_deck_button_mappings[]: {BTN, byte, bit}
        kernel = {
            "R2": (8, 0), "L2": (8, 1), "R1": (8, 2), "L1": (8, 3), "Y": (8, 4), "B": (8, 5),
            "X": (8, 6), "A": (8, 7), "View": (9, 4), "Guide": (9, 5), "Menu": (9, 6),
            "L5": (9, 7), "R5": (10, 0), "L3": (10, 6), "R3": (11, 2),
            "D-pad Up": (9, 0), "D-pad Right": (9, 1), "D-pad Left": (9, 2), "D-pad Down": (9, 3),
            "Left pad click": (10, 1), "Right pad click": (10, 2),
            "L4": (13, 1), "R4": (13, 2), "QAM": (14, 2),
        }
        # SDL_hidapi_steamdeck.c STEAMDECK_LBUTTON_* / HBUTTON_* (HBUTTON is the high word)
        sdl = {
            "R2": 0x1, "L2": 0x2, "R1": 0x4, "L1": 0x8, "Y": 0x10, "B": 0x20, "X": 0x40, "A": 0x80,
            "D-pad Up": 0x100, "D-pad Right": 0x200, "D-pad Left": 0x400, "D-pad Down": 0x800,
            "View": 0x1000, "Guide": 0x2000, "Menu": 0x4000, "L5": 0x8000, "R5": 0x10000,
            "Left pad click": 0x20000, "Right pad click": 0x40000, "L3": 0x400000, "R3": 0x4000000,
            "L4": 0x200 << 32, "R4": 0x400 << 32, "QAM": 0x40000 << 32,
        }
        ours = {name: bit for bit, name in ui.DECK_BUTTONS.items()}
        self.assertEqual(set(ours), set(kernel))
        self.assertEqual(set(ours), set(sdl))
        for name, bit in ours.items():
            self.assertEqual((kernel[name][0] - 8) * 8 + kernel[name][1], bit, name)
            self.assertEqual(sdl[name], 1 << bit, name)


PROC_SAMPLE = """I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
H: Handlers=kbd event0
B: PROP=0
B: EV=3
B: KEY={power}

I: Bus=0003 Vendor=046d Product=c31c Version=0110
N: Name="Logitech USB Keyboard"
H: Handlers=sysrq kbd event3 leds
B: EV=120013
B: KEY={kbd}

I: Bus=0003 Vendor=046d Product=c077 Version=0111
N: Name="Logitech USB Optical Mouse"
H: Handlers=mouse0 event4
B: EV=17
B: KEY={mouse}
B: REL=903

I: Bus=0003 Vendor=054c Product=0ce6 Version=8111
N: Name="Wireless Controller"
H: Handlers=event5 js0
B: EV=20000b
B: KEY={pad}

I: Bus=0003 Vendor=28de Product=11ff Version=0001
N: Name="Microsoft X-Box 360 pad 0"
H: Handlers=event6 js1
B: EV=20000b
B: KEY={pad}

I: Bus=0003 Vendor=28de Product=1205 Version=0111
N: Name="Valve Software Steam Controller"
H: Handlers=mouse1 event7
B: EV=17
B: KEY={mouse}
B: REL=903

I: Bus=0003 Vendor=28de Product=1205 Version=0111
N: Name="Valve Software Steam Controller"
H: Handlers=sysrq kbd event8 leds
B: EV=120013
B: KEY={kbd}
""".format(power=bitmap(116), kbd=bitmap(*range(1, 60), 57, 30, 44), mouse=bitmap(0x110, 0x111, 0x112),
           pad=bitmap(0x130, 0x131, 0x134, 0x133, 0x136, 0x137))


class ProcParsing(unittest.TestCase):
    def test_classification(self):
        got = {d["event"]: d["kind"] for d in ui.parse_proc_devices(PROC_SAMPLE)}
        self.assertEqual(got["event0"], None)          # power button
        self.assertEqual(got["event3"], "keyboard")
        self.assertEqual(got["event4"], "mouse")
        self.assertEqual(got["event5"], "pad")
        self.assertEqual(got["event6"], "pad")         # Steam virtual pad is kept for now
        self.assertEqual(got["event7"], None)          # Valve emulated mouse excluded
        self.assertEqual(got["event8"], None)          # Valve emulated keyboard excluded


class _Env:
    """An event loop plus helpers to push synthetic events through pipes."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.calls = []
        self.hub = ui.InputHub(self.loop, self.calls.append)
        self.writers = {}

    def add(self, name, kind):
        r, w = os.pipe()
        self.hub.add_source(r, name, kind)
        self.writers[name] = w
        self.hub._running = True

    def send(self, name, data):
        os.write(self.writers[name], data)
        self.loop.run_until_complete(asyncio.sleep(0.03))

    def close(self):
        self.hub._running = False
        for fd in list(self.hub._sources):
            self.hub._drop(fd)
        for w in self.writers.values():
            os.close(w)
        self.loop.close()


class Hub(unittest.TestCase):
    def setUp(self):
        self.e = _Env()
        self.e.add("kbd", "keyboard")
        self.e.add("mouse", "mouse")
        self.e.add("deck", "deck")
        self.e.add("pad", "pad")

    def tearDown(self):
        self.e.close()

    def test_keyboard_combination(self):
        self.e.hub.set_activator(["key:29", "key:67"])           # Left Ctrl + F9
        self.e.send("kbd", ev(1, 29, 1))
        self.assertEqual(self.e.calls, [])                       # only half held
        self.e.send("kbd", ev(1, 67, 1))
        self.assertEqual(self.e.calls, [True])
        self.e.send("kbd", ev(1, 67, 2))                         # auto-repeat is ignored
        self.assertEqual(self.e.calls, [True])
        self.e.send("kbd", ev(1, 67, 0))
        self.assertEqual(self.e.calls, [True, False])

    def test_mouse_button(self):
        self.e.hub.set_activator(["mouse:275"])                  # side button
        self.e.send("mouse", ev(1, 275, 1) + ev(0, 0, 0))
        self.assertEqual(self.e.calls, [True])
        self.e.send("mouse", ev(1, 275, 0))
        self.assertEqual(self.e.calls, [True, False])
        self.e.send("mouse", ev(2, 0, 5))                        # motion does nothing
        self.assertEqual(self.e.calls, [True, False])

    def test_deck_back_buttons(self):
        self.e.hub.set_activator(["pad:R4", "pad:R5"])
        self.e.send("deck", deck_report(42))
        self.assertEqual(self.e.calls, [])
        self.e.send("deck", deck_report(42, 16))
        self.assertEqual(self.e.calls, [True])
        self.e.send("deck", deck_report(16))
        self.assertEqual(self.e.calls, [True, False])

    def test_deck_ignores_foreign_reports(self):
        self.e.hub.set_activator(["pad:A"])
        self.e.send("deck", b"\x01\x00\x05\x40" + bytes(60))     # not a state report
        self.e.send("deck", bytes(8))
        self.assertEqual(self.e.calls, [])

    def test_generic_gamepad_and_hat(self):
        self.e.hub.set_activator(["pad:L1", "pad:D-pad Left"])
        self.e.send("pad", ev(1, 0x136, 1))                      # BTN_TL
        self.e.send("pad", ev(3, 0x10, -1))                      # ABS_HAT0X left
        self.assertEqual(self.e.calls, [True])
        self.e.send("pad", ev(3, 0x10, 0))
        self.assertEqual(self.e.calls, [True, False])

    def test_unknown_gamepad_button_gets_a_name(self):
        self.e.hub.set_activator(["pad:TRIGGER_HAPPY1"])         # e.g. a handheld's extra paddle
        self.e.send("pad", ev(1, 0x2C0, 1))
        self.assertEqual(self.e.calls, [True])

    def test_mixed_sources_in_one_combination(self):
        self.e.hub.set_activator(["key:29", "mouse:275", "pad:R4"])
        self.e.send("kbd", ev(1, 29, 1))
        self.e.send("mouse", ev(1, 275, 1))
        self.assertEqual(self.e.calls, [])
        self.e.send("deck", deck_report(42))
        self.assertEqual(self.e.calls, [True])

    def test_syn_dropped_releases(self):
        self.e.hub.set_activator(["key:67"])
        self.e.send("kbd", ev(1, 67, 1))
        self.assertEqual(self.e.calls, [True])
        self.e.send("kbd", ev(0, 3, 0))                          # SYN_DROPPED
        self.assertEqual(self.e.calls, [True, False])

    def test_dead_device_releases_and_is_dropped(self):
        self.e.hub.set_activator(["key:67"])
        self.e.send("kbd", ev(1, 67, 1))
        fd = next(f for f, s in self.e.hub._sources.items() if s.key == "kbd")
        os.close(fd)                                             # simulate unplug (read raises OSError)
        self.e.hub._readable(fd)
        self.assertEqual(self.e.calls, [True, False])
        self.assertNotIn(fd, self.e.hub._sources)

    def test_two_devices_same_key_stays_held_until_both_release(self):
        self.e.add("kbd2", "keyboard")
        self.e.hub.set_activator(["key:67"])
        self.e.send("kbd", ev(1, 67, 1))
        self.e.send("kbd2", ev(1, 67, 1))
        self.e.send("kbd", ev(1, 67, 0))
        self.assertEqual(self.e.calls, [True])
        self.e.send("kbd2", ev(1, 67, 0))
        self.assertEqual(self.e.calls, [True, False])

    def test_changing_activator_while_held_does_not_fire(self):
        self.e.send("kbd", ev(1, 67, 1))
        self.e.hub.set_activator(["key:67"])
        self.assertEqual(self.e.calls, [])
        self.e.send("kbd", ev(1, 67, 0))
        self.assertEqual(self.e.calls, [False])                  # release of an already-held activator

    def test_no_activator_never_fires(self):
        self.e.send("kbd", ev(1, 67, 1))
        self.assertEqual(self.e.calls, [])


class Capture(unittest.TestCase):
    def setUp(self):
        self.e = _Env()
        self.e.add("kbd", "keyboard")
        self.e.add("deck", "deck")
        self.done = []

    def tearDown(self):
        self.e.close()

    def start(self, grace_passed=True):
        self.e.hub.begin_capture(timeout=5, on_done=self.done.append)
        if grace_passed:
            self.e.hub._capture["t0"] -= 1.0                     # skip the start-click grace period

    def test_records_combination_on_release(self):
        self.start()
        self.e.send("deck", deck_report(42))
        self.e.send("deck", deck_report(42, 16))
        self.assertEqual(self.e.hub.capture_status()["state"], "active")
        self.assertEqual(self.e.hub.capture_status()["label"], "R4 + R5")   # live preview
        self.e.send("deck", deck_report())
        st = self.e.hub.capture_status()
        self.assertEqual((st["state"], st["tokens"]), ("done", ["pad:R4", "pad:R5"]))
        self.assertEqual(self.done, [["pad:R4", "pad:R5"]])

    def test_ignores_button_held_when_capture_started(self):
        self.e.send("deck", deck_report(7))                      # A held: it was used to click "Set"
        self.start()
        self.e.send("deck", deck_report(7, 42))                  # then R4 pressed
        self.e.send("deck", deck_report(42))                     # A released
        self.assertEqual(self.e.hub.capture_status()["state"], "active")
        self.e.send("deck", deck_report())
        self.assertEqual(self.done, [["pad:R4"]])                # A was not included

    def test_grace_period_ignores_the_starting_click(self):
        self.start(grace_passed=False)
        self.e.send("deck", deck_report(7))                      # the A press that arrives just after
        self.e.send("deck", deck_report())
        self.assertEqual(self.e.hub.capture_status()["state"], "active")
        self.assertEqual(self.done, [])

    def test_keyboard_and_controller_together(self):
        self.start()
        self.e.send("kbd", ev(1, 29, 1) + ev(1, 67, 1))
        self.e.send("deck", deck_report(42))
        self.e.send("kbd", ev(1, 29, 0) + ev(1, 67, 0))
        self.e.send("deck", deck_report())
        self.assertEqual(self.done[0], ["key:29", "key:67", "pad:R4"])

    def test_timeout_and_cancel(self):
        self.start()
        self.e.hub._capture_timeout()
        self.assertEqual(self.e.hub.capture_status()["state"], "timeout")
        self.assertEqual(self.done, [])
        self.start()
        self.e.hub.cancel_capture()
        self.assertEqual(self.e.hub.capture_status()["state"], "cancelled")

    def test_activator_does_not_fire_during_capture(self):
        calls = self.e.calls
        self.e.hub.set_activator(["key:67"])
        self.start()
        self.e.send("kbd", ev(1, 67, 1))
        self.assertEqual(calls, [])
        self.e.send("kbd", ev(1, 67, 0))
        self.assertEqual(self.done, [["key:67"]])
        self.assertEqual(calls, [])


class Lifecycle(unittest.TestCase):
    def test_discovery_and_cleanup_with_fake_filesystem(self):
        with tempfile.TemporaryDirectory() as root:
            dev = os.path.join(root, "dev")
            os.makedirs(os.path.join(dev, "input"))
            for node in ("input/event3", "input/event4", "hidraw2"):
                open(os.path.join(dev, node), "wb").close()
            proc = os.path.join(root, "devices")
            with open(proc, "w") as f:
                f.write(PROC_SAMPLE)
            hr = os.path.join(root, "sys/class/hidraw/hidraw2/device")
            os.makedirs(hr)
            with open(os.path.join(hr, "uevent"), "w") as f:
                f.write("HID_ID=0003:000028DE:00001205\nHID_NAME=x\n")
            loop = asyncio.new_event_loop()
            hub = ui.InputHub(loop, lambda a: None, proc_path=proc, dev_root=dev, sys_root=os.path.join(root, "sys"))
            found = hub._discover()
            self.assertEqual(found["input/event3"][0], "keyboard")
            self.assertEqual(found["input/event4"][0], "mouse")
            self.assertEqual(found["hidraw2"][0], "deck")
            self.assertNotIn("input/event6", found)              # Steam virtual pad skipped when a Deck is present
            hub.set_activator(["key:67"])                        # opens what exists (regular files; may not poll)
            self.assertTrue(hub.status()["running"])
            hub.set_activator([])                                # nothing to watch -> stops and closes everything
            self.assertFalse(hub.status()["running"])
            self.assertEqual(hub.status()["keyboards"] + hub.status()["mice"] + hub.status()["pads"], 0)
            loop.close()


if __name__ == "__main__":
    unittest.main()
