# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Short rumble pulses on every connected controller, so you feel that recording started
and stopped.

Uses the kernel's force-feedback interface (linux/input.h), which is the same for every
make of controller: each gamepad that advertises EV_FF gets a one-shot FF_RUMBLE effect.
Steam's emulated "X-Box 360 pad" is one of them, and Steam forwards what is played on it
to the real controller (Steam Deck, Steam Controller, PlayStation, Switch, Xbox...), so
Valve controllers work even though Steam withdraws their own evdev device.
"""
import fcntl
import os
import struct
import threading
import time

from ubermoc_input import BTN_SOUTH, _proc_bit

EV_FF = 0x15
FF_RUMBLE = 0x50
# EVIOCSFF = _IOC(_IOC_WRITE, 'E', 0x80, sizeof(struct ff_effect)); EVIOCRMFF = _IOW('E', 0x81, int)
FF_EFFECT_SIZE = 48 if struct.calcsize("P") == 8 else 44
EVIOCSFF = (1 << 30) | (FF_EFFECT_SIZE << 16) | (ord("E") << 8) | 0x80
EVIOCRMFF = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x81

# (seconds on, seconds off) per buzz. Start is one buzz, stop is two, so they feel different.
PATTERNS = {
    "start": [(0.07, 0.0)],
    "stop": [(0.06, 0.07), (0.06, 0.0)],
}
# The ten intensity steps (1..10) as motor strength out of 0xFFFF, each about 1.5x the one
# before. The top one is the strength v0.37 shipped with, which felt far too strong, so it is
# the ceiling; step 1 is barely a tick.
MAX_LEVEL = 10
LEVELS = {n: round(0x0200 * (0x6000 / 0x0200) ** ((n - 1) / (MAX_LEVEL - 1))) for n in range(1, MAX_LEVEL + 1)}
DEFAULT_LEVEL = 3


def level_of(value) -> int:
    """A stored setting as a step 1..10; anything else is the default."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value in LEVELS else DEFAULT_LEVEL


def ff_pads(proc_text: str) -> list:
    """Event nodes ('event5', ...) of the gamepads that can rumble, from /proc/bus/input/devices."""
    out = []
    for block in proc_text.split("\n\n"):
        handlers = keybits = evbits = ""
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1]
            elif line.startswith("B: KEY="):
                keybits = line.split("=", 1)[1]
            elif line.startswith("B: EV="):
                evbits = line.split("=", 1)[1]
        event = next((h for h in handlers.split() if h.startswith("event") and h[5:].isdigit()), None)
        if event and _proc_bit(evbits, EV_FF) and _proc_bit(keybits, BTN_SOUTH):
            out.append(event)
    return out


def effect_bytes(strong: int, length_ms: int) -> bytearray:
    """struct ff_effect for a new (id -1) FF_RUMBLE of this length, played at once."""
    buf = bytearray(FF_EFFECT_SIZE)
    # type, id, direction | trigger button, interval | replay length, delay
    struct.pack_into("<HhHHHHH", buf, 0, FF_RUMBLE, -1, 0, 0, 0, length_ms, 0)
    struct.pack_into("<HH", buf, 16, strong, strong)
    return buf


class Rumbler:
    """Plays the start / stop buzz on a background thread, one pattern at a time."""

    def __init__(self, find_nodes=None, dev_root="/dev", proc_path="/proc/bus/input/devices",
                 sleep=time.sleep):
        self._find_nodes = find_nodes or self._scan
        self._dev_root, self._proc_path, self._sleep = dev_root, proc_path, sleep
        self._lock = threading.Lock()

    def _scan(self) -> list:
        try:
            with open(self._proc_path, encoding="utf-8", errors="replace") as f:
                return ff_pads(f.read())
        except OSError:
            return []

    def play(self, which: str, level: int = DEFAULT_LEVEL) -> bool:
        """Start the pattern without waiting for it. False if there is nothing to buzz."""
        if which not in PATTERNS:
            return False
        nodes = self._find_nodes()
        if not nodes:
            return False
        threading.Thread(target=self._run, args=(which, nodes, LEVELS[level_of(level)]), daemon=True).start()
        return True

    def _open(self, node: str):
        try:
            return os.open(os.path.join(self._dev_root, "input", node), os.O_RDWR | os.O_CLOEXEC)
        except OSError:
            return None

    @staticmethod
    def _start(fd: int, length_ms: int, strength: int):
        """Upload the effect and play it; returns its id, or None if the pad refused."""
        buf = effect_bytes(strength, length_ms)
        try:
            fcntl.ioctl(fd, EVIOCSFF, buf, True)
            effect_id = struct.unpack_from("<h", buf, 2)[0]
            os.write(fd, struct.pack("llHHi", 0, 0, 0x15, effect_id, 1))
            return effect_id
        except OSError:
            return None

    @staticmethod
    def _erase(fd: int, effect_id):
        if effect_id is None:
            return
        try:
            fcntl.ioctl(fd, EVIOCRMFF, effect_id)    # also stops it
        except OSError:
            pass

    def _run(self, which: str, nodes: list, strength: int):
        with self._lock:
            fds = [fd for fd in (self._open(n) for n in nodes) if fd is not None]
            try:
                for on_s, off_s in PATTERNS[which]:
                    ids = [(fd, self._start(fd, int(on_s * 1000), strength)) for fd in fds]
                    self._sleep(on_s)
                    for fd, effect_id in ids:
                        self._erase(fd, effect_id)
                    if off_s:
                        self._sleep(off_s)
            finally:
                for fd in fds:
                    os.close(fd)
