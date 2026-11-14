# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Recording that stops by itself, and a live microphone level, for the voice test. Uses the same PulseAudio tools as dictation.
record_auto() takes any stream of audio chunks, so it is tested with simulated audio and no microphone."""
import array
import math
import shutil
import subprocess
import wave

RATE = 16000
CHUNK = 1600                  # 100 ms of 16 kHz mono 16-bit audio


def recorder_tool():
    """The program that can stream raw audio, or None."""
    return shutil.which("parec") or shutil.which("parecord")


def stream(env, device=None):
    """Yield (rms, peak, raw bytes) for every 100 ms of microphone audio until the caller stops iterating."""
    tool = recorder_tool()
    if not tool:
        raise RuntimeError("no parec/parecord found")
    cmd = [tool, "--raw", "--rate=16000", "--channels=1", "--format=s16le", "--latency-msec=50"]
    if device:
        cmd.insert(1, f"--device={device}")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        while True:
            raw = p.stdout.read(CHUNK * 2)
            if not raw or len(raw) < CHUNK * 2:
                return
            a = array.array("h")
            a.frombytes(raw)
            yield (sum(x * x for x in a) / len(a)) ** 0.5, max(abs(x) for x in a), raw
    finally:
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()


def db(rms):
    """Loudness on a decibel scale, -90 (silence) to 0 (as loud as the recording can get)."""
    return 20 * math.log10(max(rms, 1) / 32768)


def write_wav(path, chunks):
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(b"".join(chunks))
    w.close()


def wav_stats(path):
    """(seconds, loudest point) of a WAV, (0, 0) when unreadable."""
    try:
        w = wave.open(path)
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        return len(a) / RATE, (max(abs(x) for x in a) if a else 0)
    except Exception:  # noqa: BLE001
        return 0.0, 0


def record_auto(path, stream_fn, on_level=None, cancel=None, max_seconds=20, wait_seconds=10, tail_seconds=1.4):
    """Record until the person stops talking. Returns (reason, seconds). reason: finished | nothing-heard | too-long | cancelled.
    The clip keeps the quiet before the speech and about a second after, so nothing at the edges is cut off. on_level(rms, speaking)
    is called every 100 ms for a live level bar."""
    chunks, floors, started, loud_run, last_speech, thr = [], [], False, 0, 0.0, 250
    reason = "finished"
    for i, (rms, _pk, raw) in enumerate(stream_fn()):
        t = (i + 1) * 0.1
        chunks.append(raw)
        if i < 6:
            floors.append(rms)
            # capped: if the person starts at once, their voice must not be mistaken for the noise floor
            thr = min(1500, max(250, 5 * min(floors)))
        if rms > thr:
            loud_run += 1
            if loud_run >= 2:
                started = True
            if started:
                last_speech = t
        else:
            loud_run = 0
        if on_level:
            on_level(rms, started and t - last_speech < 0.3)
        if cancel and cancel():
            reason = "cancelled"
            break
        if started and t - last_speech > tail_seconds and t > 1.0:
            break
        if not started and t > wait_seconds:
            reason = "nothing-heard"
            break
        if t > max_seconds:
            reason = "too-long"
            break
    write_wav(path, chunks)
    return reason, len(chunks) * 0.1


def level_check(stream_fn, seconds=5.0, on_level=None):
    """Listen for a few seconds. Returns (loudest point, average of the loudest 1.5 s). A normal voice peaks at 5000 or more of 32768."""
    peak, loud = 0, []
    for i, (rms, pk, _raw) in enumerate(stream_fn()):
        peak = max(peak, pk)
        loud.append(rms)
        if on_level:
            on_level(rms, False)
        if (i + 1) * 0.1 >= seconds:
            break
    top = sorted(loud)[-15:]
    return peak, (sum(top) / len(top) if top else 0.0)


def mic_verdict(peak):
    """('nothing' | 'quiet' | 'good' | 'loud', plain-language message)."""
    if peak < 800:
        return "nothing", "Nothing was heard. Check that the microphone is on and not muted, and that its boom arm is down."
    if peak < 4000:
        return "quiet", "Too quiet. Move the microphone closer (about two fingers from your mouth) or raise its gain."
    if peak < 29000:
        return "good", "Good level."
    return "loud", "Too loud (the sound is clipping). Move the microphone a little farther away or lower its gain."
