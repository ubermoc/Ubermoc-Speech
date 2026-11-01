"""Types text and presses inputs for whatever has focus, in Game Mode and Desktop Mode.

How: the plugin backend (root) creates virtual input devices through the kernel's
uinput interface and uses them exactly as real hardware would be used: a keyboard
for typing and key presses, a mouse for mouse buttons, a gamepad for controller
buttons. Because the events enter below the display server, they reach the focused
window under gamescope (Game Mode) and under KDE on X11 or Wayland (Desktop Mode),
including Steam's own search box. Other SteamOS tools already rely on this:
InputPlumber creates uinput devices for handhelds, and Steam Button Mapper presses
keys and gamepad buttons in Game Mode through virtual devices.

The kernel interface (include/uapi/linux/uinput.h, Documentation/input/uinput.rst):
open /dev/uinput, declare event types and codes with UI_SET_EVBIT / UI_SET_KEYBIT /
UI_SET_RELBIT / UI_SET_ABSBIT (+ UI_ABS_SETUP for axes), describe the device with
UI_DEV_SETUP, create it with UI_DEV_CREATE, then write struct input_event records.

Limits: typing is by key position, so it assumes a US QWERTY layout and ASCII only
(typographic quotes/dashes are converted first). A virtual gamepad can press the
standard buttons; most games ignore the Steam Deck's extra buttons.
"""
import fcntl
import os
import re
import struct
import subprocess
import time

from ubermoc_codes import KEY_NAMES
from ubermoc_input import (
    MODIFIER_CODES,
    VIRTUAL_GAMEPAD_NAME,
    VIRTUAL_KEYBOARD_NAME as DEVICE_NAME,
    VIRTUAL_MOUSE_NAME,
    describe,
    valid_tokens,
)

# ioctl numbers, derived from linux/uinput.h ('U' base 0x55)
_IOC_WRITE = 1


def _iow(nr: int, size: int) -> int:
    return (_IOC_WRITE << 30) | (size << 16) | (0x55 << 8) | nr


UI_DEV_CREATE = 0x5501                 # _IO('U', 1)
UI_DEV_DESTROY = 0x5502                # _IO('U', 2)
UI_DEV_SETUP = 0x405C5503              # _IOW('U', 3, struct uinput_setup)  (92 bytes)
UI_SET_EVBIT = 0x40045564              # _IOW('U', 100, int)
UI_SET_KEYBIT = 0x40045565             # _IOW('U', 101, int)
UI_SET_RELBIT = 0x40045566             # _IOW('U', 102, int)
UI_SET_ABSBIT = 0x40045567             # _IOW('U', 103, int)
SETUP = struct.Struct("HHHH80sI")      # struct uinput_setup
ABS_SETUP = struct.Struct("<HHiiiiii")  # struct uinput_abs_setup: u16 code (+2 pad), then struct input_absinfo
UI_ABS_SETUP = _iow(4, 28)             # _IOW('U', 4, struct uinput_abs_setup)
assert SETUP.size == 92 and ABS_SETUP.size == 28 and UI_ABS_SETUP == 0x401C5504
assert UI_DEV_SETUP == _iow(3, SETUP.size)

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
BUS_USB = 0x03
KEY_LEFTSHIFT = 42
KEY_ENTER = 28
REL_X, REL_Y, REL_WHEEL = 0x00, 0x01, 0x08
ABS_Z, ABS_RZ = 0x02, 0x05
EVENT = struct.Struct("llHHi")         # struct input_event on 64-bit Linux
CREATE_SETTLE_S = 0.35                 # let the desktop notice a new device before the first event

_BY_NAME = {name: code for code, name in KEY_NAMES.items()}


# ---------------------------------------------------------------------------
# typing text
# ---------------------------------------------------------------------------
def _build_char_map() -> dict:
    """char -> (key code, needs shift) for a US QWERTY keyboard."""
    m = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        m[c] = (_BY_NAME[c.upper()], False)
        m[c.upper()] = (_BY_NAME[c.upper()], True)
    for c in "1234567890":
        m[c] = (_BY_NAME[c], False)
    plain = {"-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE", ";": "SEMICOLON",
             "'": "APOSTROPHE", "`": "GRAVE", "\\": "BACKSLASH", ",": "COMMA", ".": "DOT",
             "/": "SLASH", " ": "SPACE"}
    for c, name in plain.items():
        m[c] = (_BY_NAME[name], False)
    shifted = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
               "(": "9", ")": "0", "_": "MINUS", "+": "EQUAL", "{": "LEFTBRACE", "}": "RIGHTBRACE",
               ":": "SEMICOLON", '"': "APOSTROPHE", "~": "GRAVE", "|": "BACKSLASH",
               "<": "COMMA", ">": "DOT", "?": "SLASH"}
    for c, name in shifted.items():
        m[c] = (_BY_NAME[name], True)
    return m


CHAR_MAP = _build_char_map()

_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-", "…": "...", " ": " ",
    "\n": " ", "\r": " ", "\t": " ",
}


def clean_for_typing(text: str) -> str:
    """Prepare transcribed text: drop Whisper's sound annotations such as
    [BLANK_AUDIO] or [Music], convert typographic characters to plain ones, and
    flatten line breaks (a newline would press Enter and submit the field)."""
    text = re.sub(r"\[[^\]]*\]", " ", text)
    if re.fullmatch(r"\s*\([^)]*\)\s*", text):       # e.g. "(applause)" on its own
        return ""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return re.sub(r" {2,}", " ", text).strip()


# Timing by the "typing speed" setting: (seconds between typed characters, seconds an
# input pressed before/after the text is held, wait after the "before" input, wait
# before the "after" input). An input held for only a few milliseconds is missed by
# programs and games that sample once per frame (xdotool's default is about 6-12 ms,
# and reports of such misses recommend 50-150 ms), so inputs pressed around the text
# are held much longer than typed letters. The wait after the "before" input gives a
# chat box time to open.
TIMING = {
    "fast": (0.003, 0.030, 0.12, 0.04),
    "normal": (0.008, 0.060, 0.25, 0.08),
    "slow": (0.025, 0.120, 0.50, 0.15),
}
MOD_LEAD_S = 0.015          # let a modifier register before the next input goes down
RELEASE_GAP_S = 0.01


# ---------------------------------------------------------------------------
# what is pressed before / after the typed text
# ---------------------------------------------------------------------------
# An action is {"mode": "none" | "enter" | "custom", "keys": ["key:28", "mouse:272",
# "pad:A", ...]}. Anything the activator can record can be recorded here.
DEFAULT_ACTION = {"mode": "enter", "keys": []}
MAX_ACTION_INPUTS = 4

# Controller buttons a virtual gamepad can press -> (kind, kernel code).
# The gamepad copies the Xbox 360 controller (the "xpad" layout): its buttons are
# declared in the same order, and nothing numbered below them is declared, because
# SDL numbers buttons by ascending code starting at 0x120, so an extra low-numbered
# button would shift every game's controller layout. L2/R2 are axes like on the Xbox
# 360 pad (declaring BTN_TL2/TR2 would also shift SELECT/START/MODE). Grip buttons
# use the kernel's standard codes (Documentation/input/gamepad.rst; the same codes
# hid-steam uses for the Deck's R4/R5/L4/L5). QAM and the pad clicks have no standard
# code and use the first extra buttons.
PAD_OUTPUTS = {
    "A": ("key", 0x130), "B": ("key", 0x131), "Y": ("key", 0x133), "X": ("key", 0x134),
    "L1": ("key", 0x136), "R1": ("key", 0x137),
    "View": ("key", 0x13A), "Menu": ("key", 0x13B), "Guide": ("key", 0x13C),
    "L3": ("key", 0x13D), "R3": ("key", 0x13E),
    "D-pad Up": ("key", 0x220), "D-pad Down": ("key", 0x221),
    "D-pad Left": ("key", 0x222), "D-pad Right": ("key", 0x223),
    "L4": ("key", 0x224), "R4": ("key", 0x225), "L5": ("key", 0x226), "R5": ("key", 0x227),
    "QAM": ("key", 0x2C0), "Left pad click": ("key", 0x2C1), "Right pad click": ("key", 0x2C2),
    "L2": ("abs", ABS_Z), "R2": ("abs", ABS_RZ),
}
# Buttons that exist on the virtual pad but that games and Steam rarely respond to.
RARELY_HANDLED = ("L4", "R4", "L5", "R5", "QAM", "Left pad click", "Right pad click")
MOUSE_BUTTONS = tuple(range(0x110, 0x118))      # BTN_LEFT .. BTN_TASK


def normalize_action(raw) -> dict:
    """A valid action from whatever was stored (settings files can be stale)."""
    if not isinstance(raw, dict) or raw.get("mode") not in ("none", "enter", "custom"):
        return dict(DEFAULT_ACTION)
    keys = valid_tokens(raw.get("keys"))[:MAX_ACTION_INPUTS] if raw["mode"] == "custom" else []
    return {"mode": raw["mode"], "keys": keys}


# ---------------------------------------------------------------------------
# per-application overrides of the before/after inputs
# ---------------------------------------------------------------------------
# {appid: {"name": str, "kind": "steam" | "shortcut", "before": action, "after": action}}
MAX_OVERRIDES = 100


def normalize_overrides(raw) -> dict:
    """Valid overrides from whatever was stored (settings files can be stale)."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for appid, entry in raw.items():
        if not (isinstance(appid, str) and appid.isdigit() and isinstance(entry, dict)):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        out[appid] = {
            "name": name.strip()[:120],
            "kind": "shortcut" if entry.get("kind") == "shortcut" else "steam",
            "before": normalize_action(entry.get("before")),
            "after": normalize_action(entry.get("after")),
        }
        if len(out) >= MAX_OVERRIDES:
            break
    return out


def actions_for(settings: dict, appid: str):
    """(before, after, app name or None) for the running application.

    A game or app with an override uses its own before/after; everything else uses
    the global options."""
    ov = normalize_overrides(settings.get("overrides")).get(appid or "")
    if ov:
        return ov["before"], ov["after"], ov["name"]
    return normalize_action(settings.get("before")), normalize_action(settings.get("after")), None


def press_order(tokens) -> list:
    """Inputs in press order: modifier keys first, then other keys, then mouse
    buttons, then controller buttons."""
    def rank(t):
        kind, _, val = t.partition(":")
        if kind == "key":
            return (0 if int(val) in MODIFIER_CODES else 1, int(val), "")
        if kind == "mouse":
            return (2, int(val), "")
        return (3, 0, val)
    return sorted(set(tokens), key=rank)


def action_tokens(action) -> list:
    action = normalize_action(action)
    if action["mode"] == "enter":
        return [f"key:{KEY_ENTER}"]
    if action["mode"] == "custom":
        return press_order(action["keys"])
    return []


def action_label(action) -> str:
    action = normalize_action(action)
    if action["mode"] == "none":
        return "None"
    if action["mode"] == "enter":
        return "Enter"
    return describe(action["keys"]) if action["keys"] else "Not set"


def action_warnings(action) -> list:
    """Things worth telling the user about a recorded input."""
    out = []
    pads = [t[4:] for t in normalize_action(action)["keys"] if t.startswith("pad:")]
    unknown = [n for n in pads if n not in PAD_OUTPUTS]
    rare = [n for n in pads if n in RARELY_HANDLED]
    if unknown:
        out.append(f"{', '.join(unknown)} cannot be pressed on a virtual controller and will be skipped.")
    if rare:
        out.append(f"Most games and Steam do not respond to {', '.join(rare)} on a virtual controller.")
    return out


# ---------------------------------------------------------------------------
# virtual devices
# ---------------------------------------------------------------------------
class KeyboardError(Exception):
    pass


class _Device:
    """A uinput device created on first use and kept for later."""

    NAME = ""
    IDS = (BUS_USB, 0x1209, 0x5E01, 1)      # bustype, vendor, product, version
    KEYS = ()
    RELS = ()
    ABSES = ()                              # (code, minimum, maximum)

    def __init__(self, dev_path="/dev/uinput", ioctl=fcntl.ioctl, sleep=time.sleep):
        self._path = dev_path
        self._ioctl = ioctl
        self._sleep = sleep
        self._fd = None

    @property
    def created(self) -> bool:
        return self._fd is not None

    # ----- availability -----
    def check(self):
        """(available, reason). Tries to load the uinput module when the device is missing."""
        if os.path.exists(self._path):
            if os.access(self._path, os.W_OK):
                return True, ""
            return False, f"no permission to use {self._path}"
        try:
            subprocess.run(["modprobe", "uinput"], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        if os.path.exists(self._path):
            return True, ""
        return False, f"{self._path} is missing (the uinput kernel module is not available)"

    # ----- device -----
    def open(self):
        if self._fd is not None:
            return
        ok, reason = self.check()
        if not ok:
            raise KeyboardError(reason)
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as e:
            raise KeyboardError(f"cannot open {self._path}: {e}")
        try:
            if self.KEYS:
                self._ioctl(fd, UI_SET_EVBIT, EV_KEY)
                for code in self.KEYS:
                    self._ioctl(fd, UI_SET_KEYBIT, code)
            if self.RELS:
                self._ioctl(fd, UI_SET_EVBIT, EV_REL)
                for code in self.RELS:
                    self._ioctl(fd, UI_SET_RELBIT, code)
            if self.ABSES:
                self._ioctl(fd, UI_SET_EVBIT, EV_ABS)
                for code, lo, hi in self.ABSES:
                    self._ioctl(fd, UI_SET_ABSBIT, code)
                    self._ioctl(fd, UI_ABS_SETUP, ABS_SETUP.pack(code, 0, 0, lo, hi, 0, 0, 0))
            bus, vendor, product, version = self.IDS
            self._ioctl(fd, UI_DEV_SETUP, SETUP.pack(bus, vendor, product, version, self.NAME.encode(), 0))
            self._ioctl(fd, UI_DEV_CREATE)
        except OSError as e:
            os.close(fd)
            raise KeyboardError(f"could not create the virtual device: {e}")
        self._fd = fd
        self._sleep(CREATE_SETTLE_S)

    def close(self):
        if self._fd is None:
            return
        try:
            self._ioctl(self._fd, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    # ----- events -----
    def _emit(self, code: int, value: int):
        """A key/button event followed by a sync report."""
        os.write(self._fd, EVENT.pack(0, 0, EV_KEY, code, value) + EVENT.pack(0, 0, EV_SYN, 0, 0))

    def _emit_abs(self, code: int, value: int):
        os.write(self._fd, EVENT.pack(0, 0, EV_ABS, code, value) + EVENT.pack(0, 0, EV_SYN, 0, 0))


class VirtualKeyboard(_Device):
    NAME = DEVICE_NAME
    KEYS = tuple(range(1, 256))

    def type_text(self, text: str, delay_s: float = 0.008) -> dict:
        """Press the keys for `text`. Shift is held across consecutive capitals/symbols.
        Returns {typed, skipped}; characters with no key (non-ASCII) are skipped."""
        self.open()
        typed = skipped = 0
        shift = False
        try:
            for ch in text:
                mapping = CHAR_MAP.get(ch)
                if mapping is None:
                    skipped += 1
                    continue
                code, need_shift = mapping
                if need_shift != shift:
                    self._emit(KEY_LEFTSHIFT, 1 if need_shift else 0)
                    shift = need_shift
                    self._sleep(delay_s)
                self._emit(code, 1)
                self._sleep(delay_s / 2)
                self._emit(code, 0)
                self._sleep(delay_s / 2)
                typed += 1
        except OSError as e:
            self.close()                      # device went away; recreate next time
            raise KeyboardError(f"typing failed: {e}")
        finally:
            if shift and self._fd is not None:
                try:
                    self._emit(KEY_LEFTSHIFT, 0)
                except OSError:
                    pass
        return {"typed": typed, "skipped": skipped}


class VirtualMouse(_Device):
    """A mouse with the standard buttons. It declares relative axes too, as the
    kernel's own example does, so desktops treat it as a mouse; it never moves."""
    NAME = VIRTUAL_MOUSE_NAME
    IDS = (BUS_USB, 0x1209, 0x5E02, 1)
    KEYS = MOUSE_BUTTONS
    RELS = (REL_X, REL_Y, REL_WHEEL)


class VirtualGamepad(_Device):
    """An Xbox 360 style controller (045e:028e, the identity Steam Button Mapper uses
    for a pad that is recognised everywhere including in games)."""
    NAME = VIRTUAL_GAMEPAD_NAME
    IDS = (BUS_USB, 0x045E, 0x028E, 0x0110)
    KEYS = tuple(sorted({code for kind, code in PAD_OUTPUTS.values() if kind == "key"}))
    ABSES = ((ABS_Z, 0, 255), (ABS_RZ, 0, 255))


class Presser:
    """Presses a combination of keyboard keys, mouse buttons and controller buttons.

    Order (the pattern documented for virtual keyboards, e.g. ydotool's
    `29:1 56:1 59:1 59:0 56:0 29:0` for Ctrl+Alt+F1 and Steam Button Mapper's chords):
    everything goes down in order, is held, then is released in reverse order.
    """

    def __init__(self, keyboard, mouse=None, pad=None, sleep=time.sleep):
        self.keyboard = keyboard
        self.mouse = mouse or VirtualMouse()
        self.pad = pad or VirtualGamepad()
        self._sleep = sleep

    def _plan(self, tokens):
        """([(device, kind, code), ...], [names that cannot be pressed])."""
        steps, skipped = [], []
        for t in press_order(tokens):
            kind, _, val = t.partition(":")
            if kind == "key":
                steps.append((self.keyboard, "key", int(val)))
            elif kind == "mouse":
                steps.append((self.mouse, "key", int(val)))
            elif kind == "pad" and val in PAD_OUTPUTS:
                k, code = PAD_OUTPUTS[val]
                steps.append((self.pad, k, code))
            else:
                skipped.append(val if kind == "pad" else t)
        return steps, skipped

    def prepare(self, tokens):
        """Create the devices these inputs need now, so the first press is not lost
        while the desktop notices a new device."""
        steps, _ = self._plan(tokens)
        for device in {id(d): d for d, _k, _c in steps}.values():
            device.open()

    @staticmethod
    def _set(device, kind, code, down: bool):
        if kind == "abs":
            device._emit_abs(code, 255 if down else 0)
        else:
            device._emit(code, 1 if down else 0)

    def press(self, tokens, hold_s: float = 0.06) -> dict:
        """Press the inputs. Returns {"pressed": n, "skipped": [names]}."""
        steps, skipped = self._plan(tokens)
        if not steps:
            return {"pressed": 0, "skipped": skipped}
        for device in {id(d): d for d, _k, _c in steps}.values():
            device.open()
        devices = list({id(d): d for d, _k, _c in steps}.values())
        down = []
        failure = None
        try:
            for device, kind, code in steps:
                self._set(device, kind, code, True)
                down.append((device, kind, code))
                self._sleep(MOD_LEAD_S)
            self._sleep(hold_s)
        except OSError as e:
            failure = e
        # Release in reverse order whatever went down, even after a failure.
        for device, kind, code in reversed(down):
            try:
                self._set(device, kind, code, False)
            except OSError as e:
                failure = failure or e
                break
            self._sleep(RELEASE_GAP_S)
        if failure is not None:
            for device in devices:
                device.close()                # recreated on the next press
            raise KeyboardError(f"input press failed: {failure}")
        return {"pressed": len(steps), "skipped": skipped}

    def close(self):
        for device in (self.mouse, self.pad):
            device.close()
