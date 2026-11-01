"""Activator input: keyboard keys, mouse buttons and controller buttons, read by
the plugin backend so they work while a game has focus.

Why the backend and not the panel:
  * The Steam client's controller feed (SteamClient.Input) stops delivering
    buttons while a game has focus, and a game takes keyboard focus away from
    the panel, so anything read in the panel goes silent exactly when it is needed.
  * Reading is PASSIVE. Nothing is grabbed (no EVIOCGRAB) and nothing is written
    to any device: the kernel delivers every event/report to every reader, so the
    game and Steam keep receiving all input.

Sources (each constant below names where it comes from):
  * keyboards / mice / generic gamepads: /dev/input/event*   (evdev)
  * Steam Deck built-in controller:      /dev/hidraw*       (raw HID reports).
    While Steam is running its client holds the hidraw node, and the kernel
    driver (drivers/hid/hid-steam.c) then withdraws the Deck's evdev gamepad, so
    evdev cannot be used for the built-in controller.

Privacy: this module reads every keyboard. It never logs, stores or exposes a
key/button except as the "held" set compared against the user's chosen
activator, and it only opens devices while an activator is set or being captured.
"""
import asyncio
import glob
import os
import re
import struct
import time

from ubermoc_codes import BTN_NAMES, KEY_NAMES

# --- linux/input-event-codes.h -------------------------------------------------
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_DROPPED = 3
KEY_A, KEY_Z, KEY_SPACE = 30, 44, 57
BTN_LEFT, BTN_RIGHT = 0x110, 0x111
BTN_MOUSE_FIRST, BTN_MOUSE_LAST = 0x110, 0x117        # BTN_LEFT .. BTN_TASK
BTN_SOUTH = 0x130
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11

# struct input_event on 64-bit Linux: timeval (2 x long) + type, code (u16) + value (s32)
EVENT = struct.Struct("llHHi")

VENDOR_VALVE = 0x28DE
PRODUCT_DECK = 0x1205            # Steam Deck controller (hidraw)
PRODUCT_STEAM_VIRTUAL_PAD = 0x11FF   # Steam's emulated "X-Box 360 pad N"

# --- Steam Deck raw report ----------------------------------------------------
# Layout confirmed in two independent drivers:
#   Linux drivers/hid/hid-steam.c   steam_raw_event(): size 64, data[0]==1, data[1]==0,
#                                   data[2]==9 (ID_CONTROLLER_DECK_STATE)
#   SDL src/joystick/hidapi/steam/controller_structs.h: header = u16 version(1),
#                                   u8 type(9), u8 length(64); then u32 packet
#                                   number; then u64 button bits (little endian)
# i.e. the 64-bit button mask is at bytes 8..15 of the 64-byte report.
DECK_REPORT_LEN = 64
DECK_BUTTON_OFFSET = 8
# bit -> name. hid-steam.c steam_deck_button_mappings[] gives {byte, bit}; with
# bit = (byte - 8) * 8 + bit_in_byte it matches SDL's STEAMDECK_LBUTTON_* /
# STEAMDECK_HBUTTON_* (high word shifted by 32) on every entry.
DECK_BUTTONS = {
    0: "R2", 1: "L2", 2: "R1", 3: "L1",
    4: "Y", 5: "B", 6: "X", 7: "A",
    8: "D-pad Up", 9: "D-pad Right", 10: "D-pad Left", 11: "D-pad Down",
    12: "View", 13: "Guide", 14: "Menu",
    15: "L5", 16: "R5",
    17: "Left pad click", 18: "Right pad click",
    22: "L3", 26: "R3",
    41: "L4", 42: "R4",
    50: "QAM",
}

# --- generic gamepads (evdev), kernel BTN_* -> the same names as above ----------
EVDEV_PAD_NAMES = {
    0x130: "A", 0x131: "B", 0x134: "X", 0x133: "Y",      # SOUTH EAST WEST NORTH
    0x136: "L1", 0x137: "R1", 0x138: "L2", 0x139: "R2",  # TL TR TL2 TR2
    0x13A: "View", 0x13B: "Menu", 0x13C: "Guide",        # SELECT START MODE
    0x13D: "L3", 0x13E: "R3",                            # THUMBL THUMBR
    0x220: "D-pad Up", 0x221: "D-pad Down", 0x222: "D-pad Left", 0x223: "D-pad Right",
}

# --- labels ------------------------------------------------------------------
KEY_FRIENDLY = {
    "LEFTCTRL": "Left Ctrl", "RIGHTCTRL": "Right Ctrl",
    "LEFTSHIFT": "Left Shift", "RIGHTSHIFT": "Right Shift",
    "LEFTALT": "Left Alt", "RIGHTALT": "Right Alt",
    "LEFTMETA": "Left Super", "RIGHTMETA": "Right Super",
    "ESC": "Esc", "BACKSPACE": "Backspace", "ENTER": "Enter", "SPACE": "Space",
    "TAB": "Tab", "CAPSLOCK": "Caps Lock", "SCROLLLOCK": "Scroll Lock", "NUMLOCK": "Num Lock",
    "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
    "PAGEUP": "Page Up", "PAGEDOWN": "Page Down", "HOME": "Home", "END": "End",
    "INSERT": "Insert", "DELETE": "Delete", "SYSRQ": "Print Screen", "PAUSE": "Pause",
    "GRAVE": "`", "MINUS": "-", "EQUAL": "=", "LEFTBRACE": "[", "RIGHTBRACE": "]",
    "BACKSLASH": "\\", "SEMICOLON": ";", "APOSTROPHE": "'", "COMMA": ",", "DOT": ".",
    "SLASH": "/",
}
MOUSE_FRIENDLY = {
    BTN_LEFT: "Left click", BTN_RIGHT: "Right click", 0x112: "Middle click",
    0x113: "Mouse side button", 0x114: "Mouse extra button",
    0x115: "Mouse forward button", 0x116: "Mouse back button", 0x117: "Mouse task button",
}
MODIFIER_CODES = {29, 42, 54, 56, 97, 100, 125, 126}
NOISY_MOUSE = {BTN_LEFT, BTN_RIGHT, 0x112}   # used constantly in games


def key_token(code: int) -> str:
    return f"key:{code}"


def mouse_token(code: int) -> str:
    return f"mouse:{code}"


def pad_token(name: str) -> str:
    return f"pad:{name}"


def label_for(token: str) -> str:
    kind, _, val = token.partition(":")
    if kind == "key":
        raw = KEY_NAMES.get(int(val), f"KEY_{val}")
        return KEY_FRIENDLY.get(raw, raw)
    if kind == "mouse":
        return MOUSE_FRIENDLY.get(int(val), BTN_NAMES.get(int(val), f"Mouse button {val}"))
    return val


def _sort_key(token: str):
    kind, _, val = token.partition(":")
    if kind == "key":
        code = int(val)
        return (0, 0 if code in MODIFIER_CODES else 1, code, "")
    if kind == "mouse":
        return (1, 0, int(val), "")
    return (2, 0, 0, val)


def describe(tokens) -> str:
    """Human text for an activator, e.g. 'Left Ctrl + F9' or 'R4 + R5'."""
    return " + ".join(label_for(t) for t in sorted(tokens, key=_sort_key))


def valid_tokens(tokens) -> list:
    """Keep only well-formed tokens (settings files can be stale or hand-edited)."""
    out = []
    if not isinstance(tokens, (list, tuple, set, frozenset)):
        return out
    for t in tokens:
        if not isinstance(t, str) or ":" not in t:
            continue
        kind, _, val = t.partition(":")
        if kind in ("key", "mouse") and val.isdigit() and t not in out:
            out.append(t)
        elif kind == "pad" and val and len(val) < 40 and t not in out:
            out.append(t)
    return sorted(out, key=_sort_key)[:8]


def warnings_for(tokens) -> list:
    out = []
    toks = list(tokens)
    if len(toks) == 1 and toks[0].startswith("mouse:") and int(toks[0][6:]) in NOISY_MOUSE:
        out.append("This mouse button is used constantly in games. A combination or a side button is safer.")
    if len(toks) == 1 and toks[0].startswith("key:") and int(toks[0][4:]) in MODIFIER_CODES:
        out.append("A modifier key on its own triggers during normal typing and shortcuts.")
    return out


# ---------------------------------------------------------------------------
# parsing (pure functions, unit-tested without any device)
# ---------------------------------------------------------------------------
def parse_deck_report(data: bytes):
    """Held button names from a Deck state report, or None if it is not one."""
    if (len(data) != DECK_REPORT_LEN or data[0] != 1 or data[1] != 0
            or data[2] != 9 or data[3] != DECK_REPORT_LEN):
        return None
    mask = int.from_bytes(data[DECK_BUTTON_OFFSET:DECK_BUTTON_OFFSET + 8], "little")
    return frozenset(pad_token(name) for bit, name in DECK_BUTTONS.items() if mask >> bit & 1)


def _proc_bit(bitmap: str, bit: int) -> bool:
    """Test a bit in a `B: KEY=...` bitmap from /proc/bus/input/devices.

    The kernel prints unsigned-long-sized hex words, most significant first, so
    the LAST word holds bits 0..(word-1).
    """
    words = bitmap.split()
    word_bits = 8 * struct.calcsize("L")
    idx = bit // word_bits
    if idx >= len(words):
        return False
    try:
        return bool((int(words[len(words) - 1 - idx], 16) >> (bit % word_bits)) & 1)
    except ValueError:
        return False


def parse_proc_devices(text: str) -> list:
    """Input devices listed by /proc/bus/input/devices.

    Each: {name, vendor, product, event (e.g. 'event3'), kind}, kind being
    'keyboard', 'mouse', 'pad' or None. Valve-made devices (Steam's emulated
    keyboard/mouse of the Deck controller) are never 'keyboard'/'mouse'.
    """
    out = []
    for block in text.split("\n\n"):
        name, vendor, product, handlers, keybits, evbits = "", 0, 0, "", "", ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1]
            elif line.startswith("B: KEY="):
                keybits = line.split("=", 1)[1]
            elif line.startswith("B: EV="):
                evbits = line.split("=", 1)[1]
            elif line.startswith("I: "):
                mv = re.search(r"Vendor=([0-9a-fA-F]+)", line)
                mp = re.search(r"Product=([0-9a-fA-F]+)", line)
                vendor = int(mv.group(1), 16) if mv else 0
                product = int(mp.group(1), 16) if mp else 0
        event = next((h for h in handlers.split() if re.fullmatch(r"event\d+", h)), None)
        if not event:
            continue
        kind = None
        if _proc_bit(keybits, BTN_SOUTH):
            kind = "pad"
        elif all(_proc_bit(keybits, c) for c in (KEY_A, KEY_Z, KEY_SPACE)):
            kind = "keyboard"
        elif _proc_bit(keybits, BTN_LEFT) and _proc_bit(keybits, BTN_RIGHT) and _proc_bit(evbits, EV_REL):
            kind = "mouse"
        if vendor == VENDOR_VALVE and not (kind == "pad" and product == PRODUCT_STEAM_VIRTUAL_PAD):
            kind = None
        out.append({"name": name, "vendor": vendor, "product": product, "event": event, "kind": kind})
    return out


def deck_hidraw_nodes(sys_root: str = "/sys") -> list:
    """hidraw node names (e.g. 'hidraw2') that belong to a Steam Deck controller."""
    out = []
    for dev in sorted(glob.glob(os.path.join(sys_root, "class/hidraw/hidraw*"))):
        try:
            with open(os.path.join(dev, "device", "uevent")) as f:
                uevent = f.read()
        except OSError:
            continue
        m = re.search(r"^HID_ID=[0-9A-Fa-f]+:([0-9A-Fa-f]+):([0-9A-Fa-f]+)$", uevent, re.M)
        if m and int(m.group(1), 16) == VENDOR_VALVE and int(m.group(2), 16) == PRODUCT_DECK:
            out.append(os.path.basename(dev))
    return out


# ---------------------------------------------------------------------------
# the hub
# ---------------------------------------------------------------------------
class _Source:
    __slots__ = ("fd", "key", "kind", "name", "held")

    def __init__(self, fd, key, kind, name=""):
        self.fd, self.key, self.kind, self.name = fd, key, kind, name
        self.held = set()


class InputHub:
    """Watches input devices and reports when the activator combination is held.

    on_change(active: bool) is called on the event loop when the activator
    becomes held / released.
    """

    GRACE_S = 0.25          # presses this soon after capture starts are the click that started it
    RESCAN_S = 10.0         # pick up hot-plugged / renumbered devices (also after resume)

    def __init__(self, loop, on_change, proc_path="/proc/bus/input/devices",
                 dev_root="/dev", sys_root="/sys"):
        self._loop = loop
        self._on_change = on_change
        self._proc_path, self._dev_root, self._sys_root = proc_path, dev_root, sys_root
        self._sources = {}
        self._activator = frozenset()
        self._active = False
        self._held = frozenset()
        self._running = False
        self._timer = None
        self._capture = None
        self._unreadable = 0

    # ----- configuration -----
    def set_activator(self, tokens):
        self._activator = frozenset(tokens)
        # Changing the activator never fires by itself.
        self._active = bool(self._activator) and self._activator <= self._held
        if self._activator:
            self.start()
        else:
            self._maybe_stop()

    @property
    def activator(self):
        return sorted(self._activator, key=_sort_key)

    @property
    def active(self):
        return self._active

    # ----- lifecycle -----
    def start(self):
        if self._running:
            return
        self._running = True
        self.rescan()
        self._schedule()

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        for fd in list(self._sources):
            self._drop(fd)
        was_active = self._active
        self._held = frozenset()
        self._active = False
        if was_active:
            self._on_change(False)

    def _maybe_stop(self):
        if self._running and not self._activator and not self._capture_active():
            self.stop()

    def _schedule(self):
        if self._running:
            self._timer = self._loop.call_later(self.RESCAN_S, self._periodic)

    def _periodic(self):
        if self._running:
            self.rescan()
            self._schedule()

    # ----- devices -----
    def _discover(self):
        """{node: (kind, name)} for everything we want to read."""
        found = {}
        try:
            with open(self._proc_path, encoding="utf-8", errors="replace") as f:
                devices = parse_proc_devices(f.read())
        except OSError:
            devices = []
        decks = deck_hidraw_nodes(self._sys_root)
        for node in decks:
            found[node] = ("deck", "Steam Deck controller")
        for d in devices:
            if not d["kind"]:
                continue
            # With the Deck's own controller covered by hidraw, Steam's emulated pad
            # would only echo remapped output; ignore it. For other controllers that
            # Steam has taken over it is the only way to see their buttons.
            if d["vendor"] == VENDOR_VALVE and decks:
                continue
            found["input/" + d["event"]] = (d["kind"], d["name"])
        return found

    def rescan(self):
        wanted = self._discover()          # {key: (kind, name)}; key like "input/event3" or "hidraw2"
        changed = False
        for fd, src in list(self._sources.items()):
            if src.key not in wanted:
                self._drop(fd)
                changed = True
        have = {s.key for s in self._sources.values()}
        self._unreadable = 0
        for key, (kind, name) in wanted.items():
            if key in have:
                continue
            try:
                fd = os.open(os.path.join(self._dev_root, key), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError:
                self._unreadable += 1
                continue
            self.add_source(fd, key, kind, name)
        if changed:
            self._recompute()

    def add_source(self, fd, key, kind, name=""):
        """Register an already-open descriptor (also used by the tests)."""
        os.set_blocking(fd, False)
        self._sources[fd] = _Source(fd, key, kind, name)
        try:
            self._loop.add_reader(fd, self._readable, fd)
        except Exception:
            self._sources.pop(fd, None)
            os.close(fd)

    def _drop(self, fd):
        try:
            self._loop.remove_reader(fd)
        except Exception:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        self._sources.pop(fd, None)

    # ----- reading -----
    def _readable(self, fd):
        src = self._sources.get(fd)
        if src is None:
            return
        changed = False
        while True:
            try:
                data = os.read(fd, EVENT.size * 64 if src.kind != "deck" else 256)
            except BlockingIOError:
                break
            except OSError:
                # unplugged, or the machine slept and the node was renumbered
                self._drop(fd)
                self._recompute()
                self._loop.call_soon(self.rescan)
                return
            if not data:
                break
            if src.kind == "deck":
                held = parse_deck_report(data)
                if held is not None and held != src.held:
                    src.held = set(held)
                    changed = True
            elif self._parse_evdev(src, data):
                changed = True
        if changed:
            self._recompute()

    def _parse_evdev(self, src, data) -> bool:
        changed = False
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _s, _us, etype, code, value = EVENT.unpack_from(data, off)
            if etype == EV_SYN and code == SYN_DROPPED:
                # the kernel dropped events, so our "held" view may be wrong
                if src.held:
                    src.held.clear()
                    changed = True
                continue
            if etype == EV_KEY:
                if value == 2:          # auto-repeat: neither press nor release
                    continue
                token = self._token(src.kind, code)
                if token is None:
                    continue
                if value and token not in src.held:
                    src.held.add(token)
                    changed = True
                elif not value and token in src.held:
                    src.held.discard(token)
                    changed = True
            elif etype == EV_ABS and src.kind == "pad" and code in (ABS_HAT0X, ABS_HAT0Y):
                neg, pos = (("D-pad Left", "D-pad Right") if code == ABS_HAT0X
                            else ("D-pad Up", "D-pad Down"))
                for name in (neg, pos):
                    src.held.discard(pad_token(name))
                if value < 0:
                    src.held.add(pad_token(neg))
                elif value > 0:
                    src.held.add(pad_token(pos))
                changed = True
        return changed

    @staticmethod
    def _token(kind, code):
        if BTN_MOUSE_FIRST <= code <= BTN_MOUSE_LAST:
            return mouse_token(code)
        if kind == "pad" and code >= 0x120:
            return pad_token(EVDEV_PAD_NAMES.get(code) or BTN_NAMES.get(code, f"BTN_{code}"))
        if code < 0x100:
            return key_token(code)
        return None

    # ----- activator + capture -----
    def _recompute(self):
        held = set()
        for src in self._sources.values():
            held |= src.held
        self._held = frozenset(held)
        if self._capture_active():
            self._capture_step()
            return
        active = bool(self._activator) and self._activator <= self._held
        if active != self._active:
            self._active = active
            self._on_change(active)

    def _capture_active(self):
        return bool(self._capture and self._capture["state"] == "active")

    def begin_capture(self, timeout=15.0, on_done=None):
        """Record the next combination the user holds and then releases."""
        self.cancel_capture()
        self.start()
        self._capture = {
            "state": "active", "pre": set(self._held), "t0": time.monotonic(),
            "acc": set(), "tokens": [], "on_done": on_done,
            "timer": self._loop.call_later(timeout, self._capture_timeout),
        }

    def _capture_step(self):
        cap = self._capture
        cap["pre"] &= self._held                    # released => no longer excluded
        fresh = set(self._held) - cap["pre"]
        if time.monotonic() - cap["t0"] < self.GRACE_S:
            cap["pre"] |= fresh                     # the click that started the capture
            fresh = set()
        cap["acc"] |= fresh
        if cap["acc"] and not (set(self._held) - cap["pre"]):
            self._finish_capture("done")

    def _capture_timeout(self):
        if self._capture_active():
            self._finish_capture("timeout")

    def cancel_capture(self):
        if self._capture_active():
            self._finish_capture("cancelled")

    def _finish_capture(self, state):
        cap = self._capture
        cap["state"] = state
        if cap.get("timer"):
            cap["timer"].cancel()
        cap["tokens"] = valid_tokens(cap["acc"]) if state == "done" else []
        cb = cap.get("on_done")
        if state == "done" and cb:
            cb(cap["tokens"])
        # while capturing, the activator was ignored; resume from the current state
        self._active = bool(self._activator) and self._activator <= self._held
        self._maybe_stop()

    def capture_status(self):
        cap = self._capture
        if not cap:
            return {"state": "idle", "tokens": [], "label": ""}
        seen = cap["acc"] if cap["state"] == "active" else cap["tokens"]
        return {"state": cap["state"], "tokens": valid_tokens(seen), "label": describe(valid_tokens(seen))}

    # ----- diagnostics -----
    def status(self):
        kinds = [s.kind for s in self._sources.values()]
        return {
            "running": self._running,
            "keyboards": kinds.count("keyboard"),
            "mice": kinds.count("mouse"),
            "pads": kinds.count("pad"),
            "deck": kinds.count("deck") > 0,
            "unreadable": self._unreadable,
        }
