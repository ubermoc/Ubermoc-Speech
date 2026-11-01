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
  * Valve controllers (Steam Deck, Steam Controller 2015, Steam Controller 2026):
    /dev/hidraw* (raw HID reports). While Steam is running its client holds the
    device, and the kernel driver (drivers/hid/hid-steam.c) then withdraws the
    evdev gamepad, so evdev cannot be used for these.
  * "ui": the Steam interface's own controller feed, forwarded by the panel. It
    works for any controller Steam supports, but only while the Steam interface
    is active (not in a game), so it complements the raw readers.

While an activator is being RECORDED the keyboards, mice and gamepads that are
read through evdev are exclusively held (EVIOCGRAB), together with Valve's
emulated keyboard/mouse, so the system does not react to the keys being pressed.
The hold lasts only for the recording (at most ~15 s) and is released on any exit.

Privacy: this module reads every keyboard. It never logs, stores or exposes a
key/button except as the "held" set compared against the user's chosen
activator, and it only opens devices while an activator is set or being captured.
"""
import asyncio
import fcntl
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
# The plugin's own virtual keyboard (see ubermoc_typing). Never read as an activator
# source, so the text it types can never trigger the activator.
VIRTUAL_KEYBOARD_NAME = "Ubermoc Speech Keyboard"
VIRTUAL_MOUSE_NAME = "Ubermoc Speech Mouse"
VIRTUAL_GAMEPAD_NAME = "Ubermoc Speech Gamepad"
VIRTUAL_DEVICE_NAMES = {VIRTUAL_KEYBOARD_NAME, VIRTUAL_MOUSE_NAME, VIRTUAL_GAMEPAD_NAME}
PRODUCT_STEAM_VIRTUAL_PAD = 0x11FF   # Steam's emulated "X-Box 360 pad N"

# --- Valve controllers (raw HID) -----------------------------------------------
# USB ids: Linux drivers/hid/hid-ids.h and SDL src/joystick/usb_ids.h.
EVIOCGRAB = 0x40044590           # _IOW('E', 0x90, int), linux/input.h
HID_MODELS = {
    0x1205: ("deck", "Steam Deck controller"),
    0x1102: ("sc", "Steam Controller"),
    0x1142: ("sc", "Steam Controller (wireless dongle)"),
    0x1302: ("triton", "Steam Controller (2026)"),         # kernel name: "Ibex"
    0x1303: ("triton", "Steam Controller (2026, Bluetooth)"),
    0x1304: ("triton", "Steam Controller (2026 dongle)"),
    0x1305: ("triton", "Steam Controller (2026 dongle)"),
}

# Steam Deck. hid-steam.c steam_raw_event(): size 64, data[0]==1, data[1]==0,
# data[2]==9 (ID_CONTROLLER_DECK_STATE). SDL controller_structs.h: u16 version(1),
# u8 type(9), u8 length, u32 packet number, then a u64 button mask (little endian):
# the mask is at bytes 8..15. hid-steam.c steam_deck_button_mappings[] ({byte, bit}
# with bit = (byte - 8) * 8 + bit_in_byte) matches SDL's STEAMDECK_LBUTTON_* /
# STEAMDECK_HBUTTON_* (high word shifted by 32) on every entry.
DECK_REPORT_LEN = 64
DECK_BUTTON_OFFSET = 8
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

# Original Steam Controller (2015). Same 64-byte framing, data[2]==1 is the input
# state, and the 24-bit button mask is at bytes 8..10 (hid-steam.c
# steam_controller_button_mappings[], cross-checked with SDL_hidapi_steam.c
# STEAM_*_MASK). Its two rear grips are the Steam "back" buttons, named L5/R5 like
# on the Deck. Bit 23 means "left pad and joystick in use": then the left pad
# click bit is really the stick click (SDL does the same swap).
SC_BUTTON_OFFSET = 8
SC_BUTTONS = {
    0: "R2", 1: "L2", 2: "R1", 3: "L1",
    4: "Y", 5: "B", 6: "X", 7: "A",
    8: "D-pad Up", 9: "D-pad Right", 10: "D-pad Left", 11: "D-pad Down",
    12: "View", 13: "Guide", 14: "Menu",
    15: "L5", 16: "R5",
    17: "Left pad click", 18: "Right pad click",
    22: "L3",
}
SC_PAD_AND_STICK_BIT = 23

# Steam Controller (2026), kernel "Ibex" / SDL "Triton". Report id in byte 0 (0x42
# USB, 0x45 Bluetooth, 0x47 timestamped), byte 1 sequence number, then a u32 button
# mask at bytes 2..5. hid-steam.c steam_ibex_button_mappings[] ({byte, bit}, bit =
# (byte - 2) * 8 + bit_in_byte), SDL TRITON_*BUTTON_* and measurements on a real
# device (Steamcord) agree on every entry. The kernel and SDL swap the names of
# the two small buttons (bits 6 and 14); SDL's names (Valve's own) are used.
TRITON_STATE_REPORTS = (0x42, 0x45, 0x47)
TRITON_WIRELESS_STATUS = 0x79        # byte 1: 1 = disconnected
TRITON_BUTTON_OFFSET = 2
TRITON_BUTTONS = {
    0: "A", 1: "B", 2: "X", 3: "Y",
    4: "QAM", 5: "R3", 6: "View", 7: "R4", 8: "R5", 9: "R1",
    10: "D-pad Down", 11: "D-pad Right", 12: "D-pad Left", 13: "D-pad Up",
    14: "Menu", 15: "L3", 16: "Guide", 17: "L4", 18: "L5", 19: "L1",
    22: "Right pad click", 23: "R2", 26: "Left pad click", 27: "L2",
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
def _mask_to_tokens(mask: int, table: dict) -> frozenset:
    return frozenset(pad_token(name) for bit, name in table.items() if mask >> bit & 1)


def parse_hid_report(model: str, data: bytes):
    """Held button tokens from a raw report of the given controller model, or None
    when the report is not a button state (battery, status, other interfaces)."""
    if model == "deck":
        if len(data) != DECK_REPORT_LEN or data[0] != 1 or data[1] != 0 or data[2] != 9:
            return None
        mask = int.from_bytes(data[DECK_BUTTON_OFFSET:DECK_BUTTON_OFFSET + 8], "little")
        return _mask_to_tokens(mask, DECK_BUTTONS)
    if model == "sc":
        if len(data) != 64 or data[0] != 1 or data[1] != 0:
            return None
        if data[2] == 1:
            mask = int.from_bytes(data[SC_BUTTON_OFFSET:SC_BUTTON_OFFSET + 3], "little")
            held = set(_mask_to_tokens(mask, SC_BUTTONS))
            if mask >> SC_PAD_AND_STICK_BIT & 1 and pad_token("Left pad click") in held:
                held.discard(pad_token("Left pad click"))
                held.add(pad_token("L3"))
            return frozenset(held)
        if data[2] == 3 and data[4] == 1:       # wireless: controller went away
            return frozenset()
        return None
    if model == "triton":
        if len(data) >= 2 and data[0] == TRITON_WIRELESS_STATUS:
            return frozenset() if data[1] == 1 else None
        if len(data) >= 6 and data[0] in TRITON_STATE_REPORTS:
            mask = int.from_bytes(data[TRITON_BUTTON_OFFSET:TRITON_BUTTON_OFFSET + 4], "little")
            return _mask_to_tokens(mask, TRITON_BUTTONS)
        return None
    return None


def parse_deck_report(data: bytes):
    return parse_hid_report("deck", data)


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
        raw_kind = kind
        if vendor == VENDOR_VALVE and not (kind == "pad" and product == PRODUCT_STEAM_VIRTUAL_PAD):
            kind = None
        out.append({"name": name, "vendor": vendor, "product": product, "event": event,
                    "kind": kind, "raw_kind": raw_kind})
    return out


def valve_hid_nodes(sys_root: str = "/sys"):
    """Valve hidraw nodes: ({node: (model, name)}, [unrecognised 'vendor:product', ...])."""
    known, unknown = {}, []
    for dev in sorted(glob.glob(os.path.join(sys_root, "class/hidraw/hidraw*"))):
        try:
            with open(os.path.join(dev, "device", "uevent")) as f:
                uevent = f.read()
        except OSError:
            continue
        m = re.search(r"^HID_ID=[0-9A-Fa-f]+:([0-9A-Fa-f]+):([0-9A-Fa-f]+)$", uevent, re.M)
        if not m or int(m.group(1), 16) != VENDOR_VALVE:
            continue
        product = int(m.group(2), 16)
        node = os.path.basename(dev)
        if product in HID_MODELS:
            known[node] = HID_MODELS[product]
        else:
            ident = f"28de:{product:04x}"
            if ident not in unknown:
                unknown.append(ident)
    return known, unknown


# ---------------------------------------------------------------------------
# the hub
# ---------------------------------------------------------------------------
class _Source:
    __slots__ = ("fd", "key", "kind", "name", "held", "keep")

    def __init__(self, fd, key, kind, name="", keep=False):
        self.fd, self.key, self.kind, self.name, self.keep = fd, key, kind, name, keep
        self.held = set()


class InputHub:
    """Watches input devices and reports when the activator combination is held.

    on_change(active: bool) is called on the event loop when the activator
    becomes held / released.
    """

    GRACE_S = 0.25          # presses this soon after capture starts are the click that started it
    HOLD_PROMOTE_S = 0.6    # ...unless that button is kept held this long: then the user means it
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
        self._unrecognized = []
        self._emulated = []          # Valve's emulated keyboard/mouse: held only while capturing
        self._extra = {}             # fd -> key, opened just to hold them during a capture
        self._ui_held = frozenset()
        self._ui_timer = None

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

    def any_held(self, tokens) -> bool:
        """Is any of these inputs held right now?"""
        return bool(self._held & frozenset(tokens))

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
        self._release_grabs()
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
        """{key: (kind, name)} for everything we want to read. kind is keyboard, mouse,
        pad or a Valve model (deck / sc / triton)."""
        found = {}
        try:
            with open(self._proc_path, encoding="utf-8", errors="replace") as f:
                devices = parse_proc_devices(f.read())
        except OSError:
            devices = []
        known, self._unrecognized = valve_hid_nodes(self._sys_root)
        for node, (model, name) in known.items():
            found[node] = (model, name)
        self._emulated = []
        for d in devices:
            if d["name"] in VIRTUAL_DEVICE_NAMES:
                continue
            if d["vendor"] == VENDOR_VALVE and d.get("raw_kind") in ("keyboard", "mouse"):
                self._emulated.append("input/" + d["event"])
            if not d["kind"]:
                continue
            # With a Valve controller covered by its raw reports, Steam's emulated pad
            # would only echo remapped output; ignore it. For other controllers that
            # Steam has taken over it is the only way to see their buttons.
            if d["vendor"] == VENDOR_VALVE and known:
                continue
            found["input/" + d["event"]] = (d["kind"], d["name"])
        return found

    def rescan(self):
        wanted = self._discover()          # {key: (kind, name)}; key like "input/event3" or "hidraw2"
        changed = False
        for fd, src in list(self._sources.items()):
            if src.key not in wanted and not src.keep:
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

    def add_source(self, fd, key, kind, name="", keep=False):
        """Register an already-open descriptor. keep=True stops a rescan from
        dropping it (used by the tests for synthetic devices)."""
        os.set_blocking(fd, False)
        self._sources[fd] = _Source(fd, key, kind, name, keep)
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
                data = os.read(fd, EVENT.size * 64 if not self._is_hid(src) else 256)
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
            if self._is_hid(src):
                held = parse_hid_report(src.kind, data)
                if held is not None and held != src.held:
                    src.held = set(held)
                    changed = True
            elif self._parse_evdev(src, data):
                changed = True
        if changed:
            self._recompute()

    @staticmethod
    def _is_hid(src) -> bool:
        return src.kind in ("deck", "sc", "triton")

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
        held = set(self._ui_held)
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

    def begin_capture(self, timeout=15.0, on_done=None, purpose="activator"):
        """Record the next combination the user holds and then releases. Anything
        that can produce an input counts: keyboard keys, mouse buttons, controller
        buttons. `purpose` says what the recording is for (the panel shows it)."""
        self.cancel_capture()
        self.start()
        self.rescan()
        self._grab_all()
        self._capture = {
            "state": "active", "pre": set(self._held), "t0": time.monotonic(),
            "acc": set(), "tokens": [], "on_done": on_done, "purpose": purpose,
            "timer": self._loop.call_later(timeout, self._capture_timeout),
        }
        if self._capture["pre"]:
            self._capture["promote_timer"] = self._loop.call_later(self.HOLD_PROMOTE_S + 0.02, self._capture_promote)

    def _capture_step(self):
        """Work out what the user is pressing for the recording.

        The press that started the recording (e.g. the A that tapped "Record") is
        ignored, so a tap does not record itself. It does count when the user means
        it: pressing it again after letting go is a fresh press, and keeping it held
        for HOLD_PROMOTE_S (alone or together with other inputs) makes it part of
        the combination."""
        cap = self._capture
        cap["pre"] &= self._held                    # released => no longer excluded
        fresh = set(self._held) - cap["pre"]
        now = time.monotonic()
        if now - cap["t0"] < self.GRACE_S:
            cap["pre"] |= fresh                     # the click that started the capture
            fresh = set()
        if cap["pre"] and now - cap["t0"] >= self.HOLD_PROMOTE_S:
            cap["acc"] |= cap["pre"]                # held on purpose, not just a tap
            cap["pre"] = set()
        cap["acc"] |= fresh
        if cap["acc"] and not (set(self._held) - cap["pre"]):
            self._finish_capture("done")

    def _capture_promote(self):
        if self._capture_active():
            self._capture_step()

    def _capture_timeout(self):
        if self._capture_active():
            self._finish_capture("timeout")

    def cancel_capture(self):
        if self._capture_active():
            self._finish_capture("cancelled")

    def _finish_capture(self, state):
        cap = self._capture
        cap["state"] = state
        for name in ("timer", "promote_timer"):
            if cap.get(name):
                cap[name].cancel()
        cap["tokens"] = valid_tokens(cap["acc"]) if state == "done" else []
        cb = cap.get("on_done")
        if state == "done" and cb:
            cb(cap["tokens"])
        self._release_grabs()
        # while capturing, the activator was ignored; resume from the current state
        self._active = bool(self._activator) and self._activator <= self._held
        self._maybe_stop()

    def capture_status(self):
        cap = self._capture
        if not cap:
            return {"state": "idle", "tokens": [], "label": "", "purpose": ""}
        seen = cap["acc"] if cap["state"] == "active" else cap["tokens"]
        return {"state": cap["state"], "tokens": valid_tokens(seen), "label": describe(valid_tokens(seen)),
                "purpose": cap.get("purpose", "")}

    # ----- exclusive hold while recording an activator -----
    @staticmethod
    def _grab(fd, on: bool) -> bool:
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)
            return True
        except OSError:
            return False          # not an evdev node, or another program holds it

    def _grab_all(self):
        """Hold keyboards/mice/gamepads (and Valve's emulated keyboard/mouse) so the
        system does not react to the keys pressed while recording. Hidraw devices
        (Deck, Steam Controllers) cannot be held; Steam itself still sees those."""
        for src in self._sources.values():
            if not self._is_hid(src):
                self._grab(src.fd, True)
        have = {s.key for s in self._sources.values()}
        for key in self._emulated:
            if key in have or key in self._extra.values():
                continue
            try:
                fd = os.open(os.path.join(self._dev_root, key), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError:
                continue
            if self._grab(fd, True):
                self._extra[fd] = key
            else:
                os.close(fd)

    def _release_grabs(self):
        for fd, src in list(self._sources.items()):
            if not self._is_hid(src):
                self._grab(fd, False)
        for fd in list(self._extra):
            self._grab(fd, False)
            try:
                os.close(fd)
            except OSError:
                pass
        self._extra.clear()

    # ----- Steam interface feed -----
    UI_TTL_S = 2.5

    def set_ui_held(self, tokens):
        """Buttons currently held according to the Steam interface (sent by the
        panel, with a heartbeat while something is held). Expires if updates stop,
        so a lost message can never leave a button stuck."""
        toks = frozenset(t for t in valid_tokens(tokens) if t.startswith("pad:"))
        if self._ui_timer:
            self._ui_timer.cancel()
            self._ui_timer = None
        if toks:
            self._ui_timer = self._loop.call_later(self.UI_TTL_S, self.set_ui_held, [])
        if toks == self._ui_held:
            return
        self._ui_held = toks
        if self._running:
            self._recompute()

    # ----- diagnostics -----
    def status(self):
        kinds = [s.kind for s in self._sources.values()]
        controllers = [s.name for s in self._sources.values() if self._is_hid(s)]
        names = []
        for n in controllers:                 # a controller exposes several raw interfaces
            if n not in names:
                names.append(n)
        return {
            "running": self._running,
            "keyboards": kinds.count("keyboard"),
            "mice": kinds.count("mouse"),
            "pads": kinds.count("pad"),
            "controllers": names,
            "unreadable": self._unreadable,
            "unrecognized": list(self._unrecognized),
        }
