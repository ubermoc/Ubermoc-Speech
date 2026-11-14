# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""The voice test: the phrases people read, how a transcript is scored, which model is best for their voice, and the disk-space check.
Plain code, no AI: the correct text is known because the person reads it. Pure functions, so it can be tested without Decky or a microphone."""
import os
import re
import shutil

# ---- the phrases: (kind, text shown, accepted answers for spelled/numbers, hint). 22 phrases, about 4 minutes.
# kinds: sentence | spelled (letters and digits, judged one symbol at a time) | numbers | homophone (sound-alike words)
LETTER_NAMES_SAY = {"A": "ay", "B": "bee", "C": "see", "D": "dee", "E": "ee", "F": "eff", "G": "gee", "H": "aitch", "I": "eye", "J": "jay", "K": "kay",
                    "L": "ell", "M": "em", "N": "en", "O": "oh", "P": "pee", "Q": "cue", "R": "are", "S": "ess", "T": "tee", "U": "you", "V": "vee",
                    "W": "double-u", "X": "ex", "Y": "why", "Z": "zee"}
DIGIT_SAY = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}


def _spelled(symbols):
    syms = symbols.split()
    hint = "say each one on its own: " + ", ".join(LETTER_NAMES_SAY.get(c) or DIGIT_SAY.get(c, c) for c in syms)
    return ("spelled", "  ".join(syms), ["".join(syms)], hint)


PHRASES = [
    ("sentence", "I'll be right back, I'm just grabbing a drink.", [], ""),
    ("sentence", "Does anyone want to run the dungeon tonight?", [], ""),
    ("sentence", "We need one more healer and a tank before we pull.", [], ""),
    ("sentence", "Meet me outside Orgrimmar at the bank in five minutes.", [], ""),
    ("sentence", "I just got a rare drop from the Molten Core raid.", [], ""),
    ("sentence", "Please check the auction house for flasks and potions.", [], ""),
    ("sentence", "Stay behind the tank and don't pull extra monsters.", [], ""),
    ("sentence", "That was a great run, thanks everyone, see you next week.", [], ""),
    ("sentence", "Okay, let's go, everyone ready? Three, two, one, pull.", [], ""),
    ("sentence", "Say that again, I couldn't hear you over the music.", [], ""),
    _spelled("B D P T"),
    _spelled("V Z C E G"),
    _spelled("M N M N"),
    _spelled("N M S F"),
    _spelled("5 9 5 9 4"),
    _spelled("B 4 D 8 P 2"),
    ("numbers", "thirteen, thirty, fourteen, forty", ["13 30 14 40", "thirteen thirty fourteen forty"], ""),
    ("numbers", "fifteen, fifty, sixteen, sixty, seventeen, seventy", ["15 50 16 60 17 70", "fifteen fifty sixteen sixty seventeen seventy"], ""),
    ("numbers", "one hundred fifty, two hundred fifteen", ["150 215", "one hundred fifty two hundred fifteen"], ""),
    ("homophone", "I want to buy two tickets, and he wants to come too.", [], ""),
    ("homophone", "There are four of us, and this one is for you.", [], ""),
    ("homophone", "She ate eight cookies.", [], ""),
]
GROUPS = ("sentence", "spelled", "numbers", "homophone")
GROUP_NAMES = {"sentence": "normal sentences", "spelled": "spelled letters and digits", "numbers": "numbers like fifteen/fifty", "homophone": "sound-alike words"}

# ---- scoring
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def cardinal(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + cardinal(n % 100))
    if n < 1000000:
        return cardinal(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + cardinal(n % 1000))
    return " ".join(ONES[int(c)] for c in str(n))


def norm(text, digits):
    """Words of a transcript. digits="cardinal": 42 -> forty two. digits="each": 5550123 -> five five five zero one two three."""
    t = text.lower().replace("-", " ").replace("o'clock", "oclock")
    if digits == "cardinal":
        t = t.replace(",", "")

    def num(m):
        s = m.group(0)
        return cardinal(int(s)) if digits == "cardinal" and len(s) < 7 else " ".join(ONES[int(c)] for c in s)
    t = re.sub(r"\d+", num, t)
    t = re.sub(r"\boh\b", "zero", t)
    t = re.sub(r"[^a-z' ]", " ", t)
    return t.split()


def edits(a, b):
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (a[i - 1] != b[j - 1]))
    return d[len(b)]


def wer(ref, hyp):
    r = norm(ref, "cardinal")
    best = min(edits(norm(ref, mode), norm(hyp, mode)) for mode in ("cardinal", "each"))
    return best, len(r)


LETTER_WORDS = {"a": "a", "ay": "a", "b": "b", "bee": "b", "be": "b", "c": "c", "see": "c", "sea": "c", "cee": "c", "d": "d", "dee": "d", "e": "e", "ee": "e",
                "f": "f", "eff": "f", "ef": "f", "g": "g", "gee": "g", "h": "h", "aitch": "h", "i": "i", "eye": "i", "j": "j", "jay": "j", "k": "k", "kay": "k",
                "l": "l", "el": "l", "ell": "l", "m": "m", "em": "m", "n": "n", "en": "n", "o": "o", "p": "p", "pee": "p", "q": "q", "cue": "q", "queue": "q",
                "r": "r", "are": "r", "ar": "r", "s": "s", "ess": "s", "es": "s", "t": "t", "tee": "t", "tea": "t", "u": "u", "you": "u", "v": "v", "vee": "v",
                "w": "w", "x": "x", "ex": "x", "y": "y", "why": "y", "z": "z", "zee": "z", "zed": "z"}
DIGIT_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine".split())}


def symbols(text, ref_symbols):
    """The letters/digits a transcript contains, one symbol each; a word that is neither counts as one '?' (an error)."""
    out = []
    for tok in re.findall(r"[A-Za-z0-9']+", text):
        low = tok.lower()
        if low == "oh":
            out.append("o" if "o" in ref_symbols else "0")
        elif low in DIGIT_WORDS:
            out.append(DIGIT_WORDS[low])
        elif low in LETTER_WORDS:
            out.append(LETTER_WORDS[low])
        elif low in ("double", "doubleu"):
            out.append("w")
        elif tok.isdigit() or (len(tok) > 1 and tok.upper() == tok and re.fullmatch(r"[A-Z0-9]+", tok)):
            out.extend(tok.lower())
        else:
            out.append("?")
    return out


def align(ref, hyp):
    """Edit distance and what was heard instead of what: (errors, [(said, heard)], [missed], [extra])."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    subs, miss, extra, i, j = [], [], [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] != hyp[j - 1]:
                subs.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            miss.append(ref[i - 1])
            i -= 1
        else:
            extra.append(hyp[j - 1])
            j -= 1
    return d[n][m], subs, miss, extra


def score_phrase(phrase, hyp):
    """(group, errors, units, swaps) for one phrase. A clip counts at most 100% wrong, so one runaway transcript cannot decide the result."""
    kind, text, accept, _hint = phrase
    if kind == "spelled":
        ref = [c.lower() for c in accept[0]]
        e, subs, _miss, _extra = align(ref, symbols(hyp, ref))
        return "spelled", min(e, len(ref)), len(ref), subs
    if kind == "numbers":
        e, n = min(((edits(norm(a, "cardinal"), norm(hyp, "cardinal")), len(norm(a, "cardinal"))) for a in accept), key=lambda x: x[0] / max(1, x[1]))
        return "numbers", min(e, n), n, []
    e, n = wer(text, hyp)
    return ("homophone" if kind == "homophone" else "sentence"), min(e, n), n, []


def summarize(rows):
    """rows: [{group, errors, units, sec}] -> {"groups": {group: error %}, "score": average of the groups, "median_s": ...}."""
    groups = {}
    for g in GROUPS:
        rs = [r for r in rows if r["group"] == g]
        if rs and sum(r["units"] for r in rs):
            groups[g] = round(100.0 * sum(r["errors"] for r in rs) / sum(r["units"] for r in rs), 1)
    secs = sorted(r["sec"] for r in rows)
    median = secs[len(secs) // 2] if secs else 0.0
    return {"groups": groups, "score": round(sum(groups.values()) / len(groups), 1) if groups else None, "median_s": round(median, 2)}


# ---- the verdict
CLOSE_POINTS = 1.5      # models this close to the best are "too close to call"


def verdict(results, margin=CLOSE_POINTS):
    """results: {model: summarize()-dict plus optional "memory_mb"}. Returns {"best", "close", "too_close", "reason", "ranking"}.
    Honest about small samples: models within `margin` points of the best are a tie, and then the faster one is suggested."""
    ok = {m: r for m, r in results.items() if r.get("score") is not None}
    if not ok:
        return {"best": None, "close": [], "too_close": False, "reason": "No model produced a result.", "ranking": []}
    ranking = sorted(ok, key=lambda m: ok[m]["score"])
    top = ok[ranking[0]]["score"]
    close = [m for m in ranking if ok[m]["score"] - top <= margin]
    if len(close) > 1:
        best = min(close, key=lambda m: (ok[m]["median_s"], ok[m].get("memory_mb", 0)))
        names = " and ".join(close) if len(close) == 2 else ", ".join(close[:-1]) + " and " + close[-1]
        reason = (f"{names} were within {margin:g} points of each other, too close to call with {len(PHRASES)} phrases. "
                  f"{best} is the faster of them, so it is suggested.")
    else:
        best = ranking[0]
        second = ranking[1] if len(ranking) > 1 else None
        reason = f"{best} made the fewest mistakes: {top:g}% wrong on average" + (f", against {ok[second]['score']:g}% for {second}." if second else ".")
    if top > 25:
        reason += " Even the best was wrong a lot: check the microphone and the noise level, then test again."
    return {"best": best, "close": close, "too_close": len(close) > 1, "reason": reason, "ranking": ranking}


# ---- disk space and the device
def free_mb(path):
    try:
        probe = path
        while probe and not os.path.exists(probe):
            probe = os.path.dirname(probe)
        return shutil.disk_usage(probe or "/").free // (1024 * 1024)
    except OSError:
        return 0


def is_handheld(product_name=None):
    """True on a Steam Deck (the product name is Jupiter or Galileo). Reads the machine's own name; False when unknown."""
    if product_name is None:
        try:
            with open("/sys/devices/virtual/dmi/id/product_name") as f:
                product_name = f.read().strip()
        except OSError:
            return False
    return product_name in ("Jupiter", "Galileo")


def space_plan(selected, installed, free, handheld=False):
    """selected: {name: size_mb}; installed: set of names. -> {"download_mb", "free_mb", "level": ok|warn|stop, "message"}.
    Keeps 2 GB free at all times; warns when the downloads would take a large share of what is left."""
    need = sum(mb for n, mb in selected.items() if n not in installed)
    after = free - need
    gb = lambda mb: f"{mb / 1024:.1f} GB"
    if need == 0:
        return {"download_mb": 0, "free_mb": free, "level": "ok", "message": "Everything you picked is already downloaded, so nothing new will be saved to this device."}
    if after < 2048:
        return {"download_mb": need, "free_mb": free, "level": "stop",
                "message": f"Not enough space: the downloads need {gb(need)} and only {gb(free)} is free (2 GB must stay free). Pick fewer or smaller models."}
    limit = 0.25 if handheld else 0.5
    if need > limit * free or after < 8192:
        who = "On a handheld, " if handheld else ""
        return {"download_mb": need, "free_mb": free, "level": "warn",
                "message": f"{who}the downloads need {gb(need)} of the {gb(free)} that is free. You can delete the models you do not keep when the test ends."}
    return {"download_mb": need, "free_mb": free, "level": "ok", "message": f"The downloads need {gb(need)}; {gb(free)} is free."}


def process_memory_mb(pid):
    """Memory held by one process: its own memory plus the GPU memory the AMD driver reports for it. 0 when unknown."""
    total = 0
    try:
        with open(f"/proc/{pid}/status") as f:
            m = re.search(r"VmRSS:\s+(\d+)", f.read())
            total += int(m.group(1)) // 1024 if m else 0
    except OSError:
        pass
    seen = set()
    try:
        for fd in os.listdir(f"/proc/{pid}/fdinfo"):
            try:
                with open(f"/proc/{pid}/fdinfo/{fd}") as f:
                    info = f.read()
            except OSError:
                continue
            if "drm-driver:\tamdgpu" not in info:
                continue
            cid = re.search(r"drm-client-id:\s*(\d+)", info)
            if cid:
                if cid.group(1) in seen:
                    continue
                seen.add(cid.group(1))
            for key in ("drm-memory-vram", "drm-memory-gtt"):
                m = re.search(key + r":\s*(\d+)\s*KiB", info)
                if m:
                    total += int(m.group(1)) // 1024
    except OSError:
        pass
    return total
