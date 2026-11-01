# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
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
                    good[:2] + b"\x0a" + good[3:]):
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



def sc_report(*bits, wireless_event=None):
    """Original Steam Controller: 64 bytes, 01 00 <type> <len>, buttons u24 at byte 8."""
    mask = 0
    for b in bits:
        mask |= 1 << b
    r = bytearray(64)
    r[0], r[1], r[2], r[3] = 1, 0, 1, 60
    r[8:11] = mask.to_bytes(3, "little")
    if wireless_event is not None:
        r[2], r[4] = 3, wireless_event
    return bytes(r)


def triton_report(*bits, rid=0x42):
    """Steam Controller (2026): id, sequence number, u32 button mask at bytes 2..5."""
    mask = 0
    for b in bits:
        mask |= 1 << b
    r = bytearray(54)
    r[0], r[1] = rid, 9
    r[2:6] = mask.to_bytes(4, "little")
    return bytes(r)


class SteamControllers(unittest.TestCase):
    def test_original_steam_controller(self):
        self.assertEqual(ui.parse_hid_report("sc", sc_report(15, 16)), {"pad:L5", "pad:R5"})  # the two grips
        self.assertEqual(ui.parse_hid_report("sc", sc_report(7, 3)), {"pad:A", "pad:L1"})
        self.assertEqual(ui.parse_hid_report("sc", sc_report()), frozenset())

    def test_original_pad_click_is_stick_click_when_pad_and_stick_are_in_use(self):
        self.assertEqual(ui.parse_hid_report("sc", sc_report(17)), {"pad:Left pad click"})
        self.assertEqual(ui.parse_hid_report("sc", sc_report(17, 23)), {"pad:L3"})

    def test_original_wireless_events(self):
        self.assertEqual(ui.parse_hid_report("sc", sc_report(wireless_event=1)), frozenset())   # went away
        self.assertIsNone(ui.parse_hid_report("sc", sc_report(wireless_event=2)))               # connected: no buttons
        self.assertIsNone(ui.parse_hid_report("sc", bytes(64)))
        self.assertIsNone(ui.parse_hid_report("sc", sc_report(1)[:63]))

    def test_2026_steam_controller(self):
        for rid in (0x42, 0x45, 0x47):
            self.assertEqual(ui.parse_hid_report("triton", triton_report(7, 8, rid=rid)), {"pad:R4", "pad:R5"})
        self.assertEqual(ui.parse_hid_report("triton", triton_report(17, 18)), {"pad:L4", "pad:L5"})
        self.assertEqual(ui.parse_hid_report("triton", triton_report()), frozenset())

    def test_2026_wireless_status_and_foreign_reports(self):
        self.assertEqual(ui.parse_hid_report("triton", bytes([0x79, 1]) + bytes(10)), frozenset())  # disconnected
        self.assertIsNone(ui.parse_hid_report("triton", bytes([0x79, 2]) + bytes(10)))              # connected
        self.assertIsNone(ui.parse_hid_report("triton", bytes([0x43]) + bytes(14)))                 # battery
        self.assertIsNone(ui.parse_hid_report("triton", bytes(3)))
        self.assertIsNone(ui.parse_hid_report("unknown-model", triton_report(0)))

    def test_original_table_matches_kernel_and_sdl(self):
        # hid-steam.c steam_controller_button_mappings[]: {BTN, byte, bit}
        kernel = {"R2": (8, 0), "L2": (8, 1), "R1": (8, 2), "L1": (8, 3), "Y": (8, 4), "B": (8, 5),
                  "X": (8, 6), "A": (8, 7), "View": (9, 4), "Guide": (9, 5), "Menu": (9, 6),
                  "L5": (9, 7), "R5": (10, 0), "L3": (10, 6),
                  "D-pad Up": (9, 0), "D-pad Right": (9, 1), "D-pad Left": (9, 2), "D-pad Down": (9, 3)}
        # SDL_hidapi_steam.c STEAM_*_MASK
        sdl = {"R2": 0x1, "L2": 0x2, "R1": 0x4, "L1": 0x8, "Y": 0x10, "B": 0x20, "X": 0x40, "A": 0x80,
               "D-pad Up": 0x100, "D-pad Right": 0x200, "D-pad Left": 0x400, "D-pad Down": 0x800,
               "View": 0x1000, "Guide": 0x2000, "Menu": 0x4000, "L5": 0x8000, "R5": 0x10000,
               "Left pad click": 0x20000, "Right pad click": 0x40000, "L3": 0x400000}
        ours = {name: bit for bit, name in ui.SC_BUTTONS.items()}
        self.assertEqual(set(ours), set(sdl))
        for name, bit in ours.items():
            self.assertEqual(sdl[name], 1 << bit, name)
            if name in kernel:
                self.assertEqual((kernel[name][0] - 8) * 8 + kernel[name][1], bit, name)

    def test_2026_table_matches_kernel_and_sdl(self):
        # hid-steam.c steam_ibex_button_mappings[]: {BTN, byte, bit}; buttons start at byte 2.
        # (the kernel calls START/SELECT what SDL calls VIEW/MENU: same two bits, 6 and 14)
        kernel = {"A": (2, 0), "B": (2, 1), "X": (2, 2), "Y": (2, 3), "QAM": (2, 4), "R3": (2, 5),
                  "View": (2, 6), "R4": (2, 7), "R5": (3, 0), "R1": (3, 1),
                  "D-pad Down": (3, 2), "D-pad Right": (3, 3), "D-pad Left": (3, 4), "D-pad Up": (3, 5),
                  "Menu": (3, 6), "L3": (3, 7), "Guide": (4, 0), "L4": (4, 1), "L5": (4, 2), "L1": (4, 3),
                  "Right pad click": (4, 6), "R2": (4, 7), "Left pad click": (5, 2), "L2": (5, 3)}
        # SDL_hidapi_steam_triton.c TRITON_LBUTTON_* / TRITON_HBUTTON_* / *_CLICK
        sdl = {"A": 0x1, "B": 0x2, "X": 0x4, "Y": 0x8, "QAM": 0x10, "R3": 0x20, "View": 0x40, "R4": 0x80,
               "R5": 0x100, "R1": 0x200, "D-pad Down": 0x400, "D-pad Right": 0x800, "D-pad Left": 0x1000,
               "D-pad Up": 0x2000, "Menu": 0x4000, "L3": 0x8000, "Guide": 0x10000, "L4": 0x20000,
               "L5": 0x40000, "L1": 0x80000, "Right pad click": 0x400000, "R2": 0x800000,
               "Left pad click": 0x4000000, "L2": 0x8000000}
        ours = {name: bit for bit, name in ui.TRITON_BUTTONS.items()}
        self.assertEqual(set(ours), set(kernel))
        self.assertEqual(set(ours), set(sdl))
        for name, bit in ours.items():
            self.assertEqual((kernel[name][0] - 2) * 8 + kernel[name][1], bit, name)
            self.assertEqual(sdl[name], 1 << bit, name)

    def test_all_steam_controller_ids_are_known(self):
        # hid-ids.h: 1102 SC, 1142 dongle, 1205 Deck, 1302 Ibex, 1303 Ibex BLE, 1304 Proteus, 1305 Nereid
        self.assertEqual(set(ui.HID_MODELS), {0x1102, 0x1142, 0x1205, 0x1302, 0x1303, 0x1304, 0x1305})


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
        self.hub.add_source(r, name, kind, keep=True)
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


class HubNewSources(unittest.TestCase):
    def setUp(self):
        self.e = _Env()

    def tearDown(self):
        self.e.close()

    def test_original_steam_controller_grips(self):
        self.e.add("hidraw1", "sc")
        self.e.hub.set_activator(["pad:L5", "pad:R5"])
        self.e.send("hidraw1", sc_report(15))
        self.assertEqual(self.e.calls, [])
        self.e.send("hidraw1", sc_report(15, 16))
        self.assertEqual(self.e.calls, [True])
        self.e.send("hidraw1", sc_report())
        self.assertEqual(self.e.calls, [True, False])

    def test_2026_steam_controller_back_buttons(self):
        self.e.add("hidraw3", "triton")
        self.e.hub.set_activator(["pad:R4", "pad:R5"])
        self.e.send("hidraw3", triton_report(7, 8))
        self.assertEqual(self.e.calls, [True])
        self.e.send("hidraw3", bytes([0x79, 1]) + bytes(20))     # controller switched off: nothing stays held
        self.assertEqual(self.e.calls, [True, False])

    def test_same_combination_works_on_any_valve_controller(self):
        for node, kind, report in (("a", "deck", deck_report(42, 16)), ("b", "triton", triton_report(7, 8))):
            e = _Env()
            e.add(node, kind)
            e.hub.set_activator(["pad:R4", "pad:R5"])
            e.send(node, report)
            self.assertEqual(e.calls, [True], kind)
            e.close()

    def test_steam_interface_feed(self):
        self.e.hub.set_activator(["pad:R4", "pad:R5"])
        self.e.hub.set_ui_held(["pad:R4"])
        self.assertEqual(self.e.calls, [])
        self.e.hub.set_ui_held(["pad:R4", "pad:R5"])
        self.assertEqual(self.e.calls, [True])
        self.e.hub.set_ui_held([])
        self.assertEqual(self.e.calls, [True, False])

    def test_steam_interface_feed_expires_so_nothing_sticks(self):
        self.e.hub.UI_TTL_S = 0.05
        self.e.hub.set_activator(["pad:A"])
        self.e.hub.set_ui_held(["pad:A"])
        self.assertEqual(self.e.calls, [True])
        self.e.loop.run_until_complete(asyncio.sleep(0.15))      # the panel stopped sending
        self.assertEqual(self.e.calls, [True, False])

    def test_steam_interface_feed_ignores_non_controller_tokens(self):
        self.e.hub.set_activator(["key:67"])
        self.e.hub.set_ui_held(["key:67"])
        self.assertEqual(self.e.calls, [])

    def test_steam_interface_feed_combines_with_raw_sources(self):
        self.e.add("kbd", "keyboard")
        self.e.hub.set_activator(["key:29", "pad:R4"])
        self.e.send("kbd", ev(1, 29, 1))
        self.e.hub.set_ui_held(["pad:R4"])
        self.assertEqual(self.e.calls, [True])

    def test_status_lists_controllers_and_unrecognised_devices(self):
        self.e.add("hidraw1", "triton")
        self.e.hub.sources = None
        for src in self.e.hub._sources.values():
            src.name = "Steam Controller (2026)"
        self.e.hub._unrecognized = ["28de:9999"]
        st = self.e.hub.status()
        self.assertEqual(st["controllers"], ["Steam Controller (2026)"])
        self.assertEqual(st["unrecognized"], ["28de:9999"])


class Grabbing(unittest.TestCase):
    def setUp(self):
        self.e = _Env()
        self.e.add("kbd", "keyboard")
        self.e.add("hidraw1", "triton")
        self.grabs = []
        self.e.hub._grab = lambda fd, on: self.grabs.append((fd, on)) or True
        self.kbd_fd = next(f for f, s in self.e.hub._sources.items() if s.key == "kbd")
        self.hid_fd = next(f for f, s in self.e.hub._sources.items() if s.key == "hidraw1")

    def tearDown(self):
        self.e.close()

    def test_capture_holds_evdev_devices_but_not_hidraw(self):
        self.e.hub.begin_capture(timeout=5)
        self.assertIn((self.kbd_fd, True), self.grabs)
        self.assertNotIn(self.hid_fd, [fd for fd, _ in self.grabs])      # hidraw cannot be held
        self.e.hub.cancel_capture()
        self.assertIn((self.kbd_fd, False), self.grabs)                  # and it is always released

    def test_hold_released_on_completion_timeout_and_stop(self):
        for ending in ("done", "timeout", "stop"):
            e = _Env()                      # a finished capture with no activator closes everything
            e.add("kbd", "keyboard")
            e.add("hidraw1", "triton")
            grabs = []
            e.hub._grab = lambda fd, on, g=grabs: g.append((fd, on)) or True
            kbd_fd = next(f for f, s in e.hub._sources.items() if s.key == "kbd")
            e.hub.begin_capture(timeout=5)
            self.assertIn((kbd_fd, True), grabs, ending)
            e.hub._capture["t0"] -= 1
            if ending == "done":
                e.send("hidraw1", triton_report(7))
                e.send("hidraw1", triton_report())
            elif ending == "timeout":
                e.hub._capture_timeout()
            else:
                e.hub.stop()
            self.assertIn((kbd_fd, False), grabs, ending)
            e.close()

    def test_emulated_valve_keyboard_mouse_are_found_for_holding(self):
        devices = ui.parse_proc_devices(PROC_SAMPLE)
        emulated = [d["event"] for d in devices if d["vendor"] == ui.VENDOR_VALVE and d["raw_kind"] in ("keyboard", "mouse")]
        self.assertEqual(sorted(emulated), ["event7", "event8"])

    def test_failed_hold_does_not_break_capture(self):
        self.e.hub._grab = lambda fd, on: False                          # e.g. another program holds the device
        done = []
        self.e.hub.begin_capture(timeout=5, on_done=done.append)
        self.e.hub._capture["t0"] -= 1
        self.e.send("kbd", ev(1, 67, 1))
        self.e.send("kbd", ev(1, 67, 0))
        self.assertEqual(done, [["key:67"]])

    def test_real_ioctl_on_a_non_device_is_harmless(self):
        r, w = os.pipe()
        self.assertFalse(ui.InputHub._grab(r, True))                     # ENOTTY, not an exception
        os.close(r)
        os.close(w)


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
            self.e.hub._capture["t0"] -= self.e.hub.GRACE_S + 0.05   # skip only the start-click grace period

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


class StartButton(unittest.TestCase):
    """The button used to start a recording: a tap is ignored, a deliberate use counts."""

    def setUp(self):
        self.e = _Env()
        self.e.add("deck", "deck")
        self.done = []

    def tearDown(self):
        self.e.close()

    def begin(self, held_at_start=True):
        if held_at_start:
            self.e.send("deck", deck_report(7))                       # A is down (it tapped "Record")
        self.e.hub.begin_capture(timeout=5, on_done=self.done.append, purpose="before")
        self.e.hub._capture["t0"] -= self.e.hub.GRACE_S + 0.05       # past the start-click grace period

    def test_a_tap_of_the_start_button_records_nothing(self):
        self.begin()
        self.e.send("deck", deck_report())                           # released quickly
        self.assertEqual(self.e.hub.capture_status()["state"], "active")
        self.assertEqual(self.done, [])

    def test_pressing_it_again_counts(self):
        self.begin()
        self.e.send("deck", deck_report())
        self.e.send("deck", deck_report(7))                          # a fresh A press
        self.e.send("deck", deck_report())
        self.assertEqual(self.done, [["pad:A"]])

    def test_holding_it_long_enough_counts(self):
        self.begin()
        self.e.hub._capture["t0"] -= self.e.hub.HOLD_PROMOTE_S       # still held after the threshold
        self.e.hub._capture_promote()                                # (the timer does this)
        self.assertEqual(self.e.hub.capture_status()["label"], "A")
        self.e.send("deck", deck_report())
        self.assertEqual(self.done, [["pad:A"]])

    def test_holding_it_with_other_inputs_makes_a_combination(self):
        self.begin()
        self.e.send("deck", deck_report(7, 42))                      # R4 pressed while A is still down
        self.assertEqual(self.e.hub.capture_status()["tokens"], ["pad:R4"])   # not yet: A may only be the tap
        self.e.hub._capture["t0"] -= self.e.hub.HOLD_PROMOTE_S
        self.e.hub._capture_promote()                                # A was held on purpose
        self.e.send("deck", deck_report())
        self.assertEqual(self.done, [["pad:A", "pad:R4"]])

    def test_a_tap_followed_quickly_by_other_buttons_does_not_record_the_tap(self):
        self.begin()
        self.e.send("deck", deck_report(7, 42))                      # A still down for a moment...
        self.e.send("deck", deck_report(42))                         # ...then released
        self.e.send("deck", deck_report())
        self.assertEqual(self.done, [["pad:R4"]])

    def test_the_hold_timer_is_cancelled_with_the_recording(self):
        self.begin()
        timer = self.e.hub._capture["promote_timer"]
        self.e.hub.cancel_capture()
        self.assertTrue(timer.cancelled())

    def test_no_timer_when_nothing_was_held(self):
        self.begin(held_at_start=False)
        self.assertNotIn("promote_timer", self.e.hub._capture)


class CapturePurpose(unittest.TestCase):
    def setUp(self):
        self.e = _Env()
        self.e.add("kbd", "keyboard")
        self.e.add("deck", "deck")
        self.e.add("mouse", "mouse")
        self.done = []

    def tearDown(self):
        self.e.close()

    def test_any_device_can_be_recorded_for_any_purpose(self):
        self.e.hub.begin_capture(timeout=5, on_done=self.done.append, purpose="before")
        self.e.hub._capture["t0"] -= 1
        self.e.send("kbd", ev(1, 29, 1))                          # a keyboard key
        self.e.send("mouse", ev(1, 275, 1))                       # a mouse button
        self.e.send("deck", deck_report(42))                      # a controller button
        self.assertEqual(self.e.hub.capture_status()["purpose"], "before")
        self.e.send("kbd", ev(1, 29, 0))
        self.e.send("mouse", ev(1, 275, 0))
        self.e.send("deck", deck_report())
        self.assertEqual(self.done, [["key:29", "mouse:275", "pad:R4"]])

    def test_status_reports_the_purpose(self):
        self.e.hub.begin_capture(timeout=5)
        self.assertEqual(self.e.hub.capture_status()["purpose"], "activator")


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
            self.assertEqual(sorted(hub._emulated), ["input/event7", "input/event8"])
            self.assertNotIn("input/event6", found)              # Steam virtual pad skipped when a Deck is present
            hub.set_activator(["key:67"])                        # opens what exists (regular files; may not poll)
            self.assertTrue(hub.status()["running"])
            hub.set_activator([])                                # nothing to watch -> stops and closes everything
            self.assertFalse(hub.status()["running"])
            self.assertEqual(hub.status()["keyboards"] + hub.status()["mice"] + hub.status()["pads"], 0)
            loop.close()


if __name__ == "__main__":
    unittest.main()
