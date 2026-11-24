# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Per-application overrides: the activator, model and typing speed of one game / app.

Pure functions over stored settings - no decky, no hardware, no devices - so all of it is
unit-tested directly. ubermoc_typing.normalize_overrides validates every stored override
(with its before / after and symbols); this module validates the three new fields and says
what applies to the application that is running now.

The rule everywhere: **a field that is absent, empty or unusable follows the Global options**,
so settings files from older versions (which have none of these fields) behave exactly as before.
"""
from ubermoc_input import valid_tokens
from ubermoc_models import MODELS

# The typing speeds the panel offers. ubermoc_typing.TIMING holds the timings for exactly
# these names (a test checks the two lists stay the same, so this module needs no import
# of ubermoc_typing).
SPEEDS = ("fast", "normal", "slow")
DEFAULT_SPEED = "normal"
# The typing methods the panel offers (same names as the global `type_method` setting).
METHODS = ("keyboard", "steam")
DEFAULT_METHOD = "keyboard"
# The rumble intensity the panel offers (1..10, the same range as the global slider).
LEVEL_MIN, LEVEL_MAX = 1, 10
# Every field the panel can set for one application. `before` / `after` go through
# set_key_action, so they are not in this list.
FIELDS = ("activator", "model", "use_secondary", "speed", "symbols", "method", "rumble", "rumble_level", "vad")


def normalize_extra(entry) -> dict:
    """The validated per-app fields of one stored override: activator, model, speed,
    spoken symbols, typing method, rumble on/off, rumble intensity, voice detector.

    A field that is absent, empty, the wrong type, not a known model, not a known speed,
    not a known method, or outside the intensity range is left out, which means "follow the
    Global options". Nothing here is invented: a damaged settings file can never store a
    combination, a model, a speed, a method, a rumble setting or a detector switch the
    plugin cannot use."""
    out = {}
    tokens = valid_tokens(entry.get("activator"))
    if tokens:
        out["activator"] = tokens
    model = entry.get("model")
    if isinstance(model, str) and model in MODELS:
        out["model"] = model
    use_secondary = entry.get("use_secondary")
    if isinstance(use_secondary, bool):
        out["use_secondary"] = use_secondary
    speed = entry.get("speed")
    if isinstance(speed, str) and speed in SPEEDS:
        out["speed"] = speed
    symbols = entry.get("symbols")
    if symbols in ("words", "symbols"):
        out["symbols"] = symbols
    method = entry.get("method")
    if method in METHODS:
        out["method"] = method
    # a real bool only: a settings file that stores "false" as a string sets nothing
    rumble = entry.get("rumble")
    if isinstance(rumble, bool):
        out["rumble"] = rumble
    level = entry.get("rumble_level")
    if isinstance(level, int) and not isinstance(level, bool) and LEVEL_MIN <= level <= LEVEL_MAX:
        out["rumble_level"] = level
    vad = entry.get("vad")
    if isinstance(vad, bool):
        out["vad"] = vad
    return out


def set_field(overrides: dict, appid: str, field: str, value) -> dict:
    """A new overrides dict with one app's field set; "" / None / junk = follow Global.

    Returns copies: the dict passed in is never modified, so a caller cannot corrupt the
    settings it loaded, and saving one field never drops another field of the same override.
    An unknown appid, an unknown field, or a non-dict entry changes nothing.
    """
    out = {str(a): dict(e) for a, e in overrides.items() if isinstance(e, dict)}
    entry = out.get(str(appid))
    if entry is None or field not in FIELDS:
        return out
    if field == "activator":
        tokens = valid_tokens(value)
        entry.pop("activator", None)
        if tokens:
            entry["activator"] = tokens
    elif field == "model":
        entry.pop("model", None)
        if isinstance(value, str) and value in MODELS:
            entry["model"] = value
    elif field == "use_secondary":
        entry.pop("use_secondary", None)
        if isinstance(value, bool):
            entry["use_secondary"] = value
    elif field == "speed":
        entry.pop("speed", None)
        if isinstance(value, str) and value in SPEEDS:
            entry["speed"] = value
    elif field == "symbols":
        entry.pop("symbols", None)
        if isinstance(value, str) and value in ("words", "symbols"):
            entry["symbols"] = value
    elif field == "method":
        entry.pop("method", None)
        if isinstance(value, str) and value in METHODS:
            entry["method"] = value
    elif field == "rumble":
        entry.pop("rumble", None)
        if isinstance(value, bool):
            entry["rumble"] = value
    elif field == "rumble_level":
        entry.pop("rumble_level", None)
        if isinstance(value, int) and not isinstance(value, bool) and LEVEL_MIN <= value <= LEVEL_MAX:
            entry["rumble_level"] = value
    elif field == "vad":
        entry.pop("vad", None)
        if isinstance(value, bool):
            entry["vad"] = value
    return out


def activator_for(override, global_tokens) -> list:
    """The combination to arm: the app's own activator, else the global one.

    `override` is one normalized override (or None for an app without one). A combination
    that is not valid after validation counts as unset: the global activator is armed."""
    tokens = valid_tokens((override or {}).get("activator"))
    return tokens if tokens else valid_tokens(global_tokens)


def speed_for(override, global_speed) -> str:
    """Typing speed for the app: its own, else the global speed (normal if that is unusable)."""
    speed = (override or {}).get("speed")
    if speed in SPEEDS:
        return speed
    return global_speed if global_speed in SPEEDS else DEFAULT_SPEED


def model_for(override, global_model, is_installed, secondary="") -> str:
    """The model to use for this app: the Secondary model when the app is set to use it, else the main (global) one.

    Checked every time, not stored: deleting the secondary model makes the app fall back to the main
    model, and re-downloading makes it use the secondary again with no setting to fix. An override that
    still carries an older per-app model (before the secondary model was global) keeps using that model."""
    name = (override or {}).get("model")
    if isinstance(name, str) and name in MODELS and is_installed(name):
        return name
    if (override or {}).get("use_secondary") and isinstance(secondary, str) and secondary in MODELS and is_installed(secondary):
        return secondary
    return global_model


def method_for(override, global_method) -> str:
    """Typing method for the app: its own, else the global one (virtual keyboard if unusable)."""
    method = (override or {}).get("method")
    if method in METHODS:
        return method
    return global_method if global_method in METHODS else DEFAULT_METHOD


def rumble_for(override, global_on) -> bool:
    """Whether the start / stop buzz is felt in this app: its own switch, else the global one."""
    rumble = (override or {}).get("rumble")
    if isinstance(rumble, bool):
        return rumble
    return not (global_on is False)


def level_for(override, global_level) -> int:
    """Rumble intensity in this app: its own, else the global level (clamped to the panel's range)."""
    level = (override or {}).get("rumble_level")
    if isinstance(level, int) and not isinstance(level, bool):
        return level
    return min(LEVEL_MAX, max(LEVEL_MIN, global_level if isinstance(global_level, int) else LEVEL_MIN))


def vad_for(override, global_on) -> bool:
    """The voice detector for this app: its own switch, else the global one for that model.

    `global_on` is what the global setting says for the model in use (the model's own
    per-model choice, and False for engines that have no detector switch)."""
    vad = (override or {}).get("vad")
    if isinstance(vad, bool):
        return vad
    return bool(global_on)


def normalize_hidden(raw) -> dict:
    """The stored hidden applications: {appid: True} for numeric appids only.

    Hiding is a panel preference and never changes an override: a hidden app's override keeps
    applying when that app runs. Junk (a non-dict, a non-numeric appid, a false value) is dropped.
    Absent = nothing is hidden, so settings from older versions behave as before."""
    if not isinstance(raw, dict):
        return {}
    return {appid: True for appid, v in raw.items()
            if isinstance(appid, str) and appid.isdigit() and v is True}


def flag_of(raw) -> bool:
    """A stored switch (show hidden, keep both models loaded): on only when stored as True."""
    return raw is True
