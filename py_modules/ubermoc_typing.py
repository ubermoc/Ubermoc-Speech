"""Types text into whatever has keyboard focus, in Game Mode and Desktop Mode.

How: the plugin backend (root) creates a virtual USB-style keyboard through the
kernel's uinput interface and presses keys on it, exactly as a real keyboard would.
Because the events enter below the display server, they reach the focused window
under gamescope (Game Mode) and under KDE on X11 or Wayland (Desktop Mode), including
Steam's own search box. Other SteamOS tools already rely on this: InputPlumber
creates uinput keyboards for handhelds, and Steam Button Mapper forwards a user's
typing into Game Mode through one.

The kernel interface (include/uapi/linux/uinput.h): open /dev/uinput, declare the
event types and keys with UI_SET_EVBIT / UI_SET_KEYBIT, describe the device with
UI_DEV_SETUP (struct uinput_setup: input_id, 80-byte name, ff_effects_max), create it
with UI_DEV_CREATE, then write struct input_event records.

Limits (by design of a keyboard): it types by key position, so it assumes a US QWERTY
layout, and it can type ASCII only (typographic quotes/dashes are converted first).
"""
import fcntl
import os
import re
import struct
import subprocess
import time

from ubermoc_codes import KEY_NAMES
from ubermoc_input import MODIFIER_CODES, VIRTUAL_KEYBOARD_NAME as DEVICE_NAME, describe, label_for, valid_tokens

# ioctl numbers, derived from linux/uinput.h ('U' base 0x55)
UI_DEV_CREATE = 0x5501                 # _IO('U', 1)
UI_DEV_DESTROY = 0x5502                # _IO('U', 2)
UI_DEV_SETUP = 0x405C5503              # _IOW('U', 3, struct uinput_setup)  (92 bytes)
UI_SET_EVBIT = 0x40045564              # _IOW('U', 100, int)
UI_SET_KEYBIT = 0x40045565             # _IOW('U', 101, int)
SETUP = struct.Struct("HHHH80sI")      # struct uinput_setup
assert SETUP.size == 92

EV_SYN, EV_KEY = 0x00, 0x01
BUS_USB = 0x03
KEY_LEFTSHIFT = 42
KEY_ENTER = 28
EVENT = struct.Struct("llHHi")         # struct input_event on 64-bit Linux
CREATE_SETTLE_S = 0.35                 # let the desktop notice the new keyboard before the first key

_BY_NAME = {name: code for code, name in KEY_NAMES.items()}


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


# Timing by the "typing speed" setting: (seconds between typed characters, seconds a
# before/after key is held, wait after the "before" key, wait before the "after" key).
# A key held for only a few milliseconds is missed by programs and games that sample
# the keyboard once per frame (xdotool's default is about 6-12 ms, and reports of such
# misses recommend 50-150 ms), so keys pressed around the text are held much longer
# than typed letters. The wait after the "before" key gives a chat box time to open.
TIMING = {
    "fast": (0.003, 0.030, 0.12, 0.04),
    "normal": (0.008, 0.060, 0.25, 0.08),
    "slow": (0.025, 0.120, 0.50, 0.15),
}
MOD_LEAD_S = 0.015          # let a modifier register before the next key goes down
RELEASE_GAP_S = 0.01

# ---- keys pressed before / after the typed text -----------------------------
# An action is {"mode": "none" | "enter" | "custom", "keys": ["key:<code>", ...]}.
DEFAULT_ACTION = {"mode": "enter", "keys": []}
MAX_ACTION_KEYS = 4


def normalize_action(raw) -> dict:
    """A valid action from whatever was stored (settings files can be stale)."""
    if not isinstance(raw, dict) or raw.get("mode") not in ("none", "enter", "custom"):
        return dict(DEFAULT_ACTION)
    keys = [t for t in valid_tokens(raw.get("keys")) if t.startswith("key:")][:MAX_ACTION_KEYS]
    return {"mode": raw["mode"], "keys": keys if raw["mode"] == "custom" else []}


def ordered_codes(tokens) -> list:
    """Key codes in press order: modifiers first, then the rest by code."""
    codes = sorted({int(t[4:]) for t in tokens if t.startswith("key:")})
    return [c for c in codes if c in MODIFIER_CODES] + [c for c in codes if c not in MODIFIER_CODES]


def action_codes(action) -> list:
    action = normalize_action(action)
    if action["mode"] == "enter":
        return [KEY_ENTER]
    if action["mode"] == "custom":
        return ordered_codes(action["keys"])
    return []


def action_label(action) -> str:
    action = normalize_action(action)
    if action["mode"] == "none":
        return "None"
    if action["mode"] == "enter":
        return "Enter"
    return describe(action["keys"]) if action["keys"] else "Not set"


# Keys offered in the picker (controller friendly), in display order.
_PICK_NAMES = (["ENTER", "TAB", "SPACE", "ESC", "BACKSPACE", "DELETE"]
               + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + list("1234567890")
               + [f"F{i}" for i in range(1, 13)]
               + ["UP", "DOWN", "LEFT", "RIGHT", "HOME", "END", "PAGEUP", "PAGEDOWN", "INSERT"]
               + ["MINUS", "EQUAL", "LEFTBRACE", "RIGHTBRACE", "SEMICOLON", "APOSTROPHE", "GRAVE",
                  "BACKSLASH", "COMMA", "DOT", "SLASH"])
KEY_OPTIONS = [{"code": _BY_NAME[n], "label": label_for(f"key:{_BY_NAME[n]}")} for n in _PICK_NAMES]
MOD_OPTIONS = [
    {"label": "No modifier", "codes": []},
    {"label": "Ctrl", "codes": [29]},
    {"label": "Shift", "codes": [42]},
    {"label": "Alt", "codes": [56]},
    {"label": "Super (Windows key)", "codes": [125]},
    {"label": "Ctrl + Shift", "codes": [29, 42]},
    {"label": "Ctrl + Alt", "codes": [29, 56]},
    {"label": "Alt + Shift", "codes": [42, 56]},
    {"label": "Ctrl + Alt + Shift", "codes": [29, 42, 56]},
]


class KeyboardError(Exception):
    pass


class VirtualKeyboard:
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
        """Create the virtual keyboard (once; it is kept for later use)."""
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
            self._ioctl(fd, UI_SET_EVBIT, EV_KEY)
            for code in range(1, 256):
                self._ioctl(fd, UI_SET_KEYBIT, code)
            self._ioctl(fd, UI_DEV_SETUP, SETUP.pack(BUS_USB, 0x1209, 0x5E01, 1, DEVICE_NAME.encode(), 0))
            self._ioctl(fd, UI_DEV_CREATE)
        except OSError as e:
            os.close(fd)
            raise KeyboardError(f"could not create the virtual keyboard: {e}")
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

    # ----- typing -----
    def _emit(self, code: int, value: int):
        os.write(self._fd, EVENT.pack(0, 0, EV_KEY, code, value) + EVENT.pack(0, 0, EV_SYN, 0, 0))

    def press_keys(self, codes, hold_s: float = 0.06):
        """Press a key or combination: modifiers go down first, the keys are held for
        `hold_s`, then everything is released in reverse order (the sequence ydotool
        documents, e.g. 29:1 56:1 59:1 59:0 56:0 29:0 for Ctrl+Alt+F1)."""
        if not codes:
            return
        self.open()
        down = []
        try:
            for code in codes:
                self._emit(code, 1)
                down.append(code)
                self._sleep(MOD_LEAD_S)
            self._sleep(hold_s)
        except OSError as e:
            self.close()
            raise KeyboardError(f"key press failed: {e}")
        finally:
            if self._fd is not None:
                for code in reversed(down):
                    try:
                        self._emit(code, 0)
                    except OSError:
                        break
                    self._sleep(RELEASE_GAP_S)

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
