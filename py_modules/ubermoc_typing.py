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
from ubermoc_input import VIRTUAL_KEYBOARD_NAME as DEVICE_NAME

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
