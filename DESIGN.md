# Applications overrides

Design for the feature; the code and README follow this. Version 0.44, nothing released.

## How it works today (checked against the code)

- **Running app**: the panel frontend reads Steam's `MainRunningApp` (`CSteamUIStore`, C1) and sends
  appid + display name to the backend through `set_running_app`. It fires when Steam announces an app
  starting/stopping (`RegisterForAppLifetimeNotifications`) and when the backend asks (`query_app`) at the
  start of each dictation. No polling. `main.py` keeps `self.running_app`; `_running_appid()` is the lookup
  (E7, E8). Desktop mode has no Steam interface, so the appid is `""`.
- **Overrides**: stored in the settings dict under `overrides` as
  `{appid: {name, kind, before, after, symbols}}`, validated by `ubermoc_typing.normalize_overrides`
  (E9), written by `SettingsStore.save` to Decky's settings folder and `~/.local/share/ubermoc-speech`.
  `actions_for(settings, appid)` / `symbols_for(settings, appid)` return the app's choice or the global one.
- **Activator**: `main.py` sets it on `ubermoc_input.InputHub` from the stored tokens at startup, after
  capture (`_captured`) and after clear (E10). `InputHub.set_activator` replaces the combination,
  recomputes whether it is currently held, and never fires the activator by itself (E11).
- **Typing speed**: global `type_speed` (`fast`/`normal`/`slow`) → `ubermoc_typing.TIMING` (E14).
- **Speech server**: `bin/whisper-server` / `bin/parakeet-server` take `--host` and `--port` (C11, C12),
  and two servers can run at the same time on two ports (C13, measured: `whisper small.en` 88 MB,
  `parakeet q8_0` 92 MB while both were answering).

## Settings format

`overrides[appid]` gains optional fields, all validated by `normalize_overrides` through
`ubermoc_apps.normalize_extra`; `FIELDS` is
`activator, model, speed, symbols, method, rumble, rumble_level, vad`:

```
{"570": {"name": "Dota 2", "kind": "steam",
         "before": {...}, "after": {...}, "symbols": "words",     # existing
         "activator": ["pad:R4", "pad:R5"],   # absent/invalid = follow Global
         "model": "small.en",                 # absent/invalid = follow Global
         "speed": "slow",                      # absent/invalid = follow Global
         "method": "steam",                    # steam | keyboard
         "rumble": false, "rumble_level": 7,   # bool, 1..10
         "vad": false}}                        # bool
```

Three settings sit outside `overrides`:

```
"hidden": {"570": true, ...},   # appid -> hidden (panel preference only)
"show_hidden": false,           # show the Hidden applications dropdown
"keep_loaded": false            # keep the global model and an app's model loaded at once
```

- **absent = follow Global.** A file from an older version loads unchanged: an override with only
  `name`/`kind`/`before`/`after`/`symbols` behaves exactly as it did, and `hidden` / `show_hidden` /
  `keep_loaded` read as empty / off.
- **Validation** (module `py_modules/ubermoc_apps.py`, no `decky` import):
  `activator` → `ubermoc_input.valid_tokens`, empty after validation = follow Global;
  `model` → a name in `ubermoc_models.MODELS`; `speed` → `fast`/`normal`/`slow`
  (a test checks this set equals `ubermoc_typing.TIMING`); `method` → `steam`/`keyboard`;
  `rumble` / `vad` → bool; `rumble_level` → int 1..10; `hidden` → appid digits.
  Anything invalid is dropped, so a hand-edited file cannot store junk.
- Every lookup makes a **fresh copy** (as `normalize_overrides` already does): a caller can never mutate
  the settings dict in place, and saving one field never drops another (`SettingsStore.save` merges).

## Fallback rules

| Field | App has a valid value | App has no value / value unusable |
|---|---|---|
| activator | app's combination (global is not armed) | global activator |
| model | app's model **and it is downloaded** | global model (`ubermoc_models.choose_model`) |
| speed | app's speed | global `type_speed` (default `normal`) |
| method | app's method | global `type_method` |
| rumble | app's on/off | global `rumble` |
| rumble_level | app's 1..10 | global `rumble_level` |
| vad | app's on/off, for a model that has a detector | global `vad` |

The model check happens at recording time, so deleting a model needs no cleanup: the app falls back to
the global model, and the field is kept so re-downloading it works again. The panel shows
"not downloaded" and says the global model is used until it is downloaded.

`set_running_app` arms the app's activator and, when `keep_loaded` is on, starts loading the app's model
in the background at once, so the first dictation in that app is instant.

## Two speech servers (`keep_loaded`)

The plugin keeps two server slots: `primary` (port 8178) and `extra` (port 8179). `_start_one` starts
one server on its slot's port, records `server_proc` / `extra_proc` (with `server_model`, `server_vad`,
`extra_model`, `extra_vad`), and returns ok once the port answers.

- `keep_loaded` off: one server, as before. The global model runs in `primary`; a model an app picked
  for itself goes into the second slot only when the switch is on. Turning the switch off frees the
  second server at once.
- `keep_loaded` on: the global model lives in `primary`, an app's own model in `extra`, so switching
  between two apps costs nothing. A slot is restarted only when it must hold a different model, or was
  started with a voice detector setting this app does not want (the detector is fixed at start, C12).
  When an app's own model is started first, the global model is brought up alongside in the background.
- `get_state` returns `model`, `loading_model` (the server for this dictation's model is not up yet) and
  `loaded_models` (the models whose servers are up), so the panel names what is ready and warns where a
  dictation will wait.
- Deleting a model, changing a model, or changing the voice detector stops only the slot running it.

## Panel: Applications overrides

- **Group header** `Applications overrides` (summary: running app, or the number of overrides).
- **Running now** row, then one **Applications** dropdown built by `src/appList.ts`: Steam games first
  (category row `Steam games`), then non-Steam apps (`Non-Steam apps`), alphabetical inside each
  category, each entry labelled `Steam - name` / `Non-Steam - name`. Hidden apps never appear in it.
  Picking an app adds its override (a copy of the global before/after and symbols) and opens its section.
- **Keep both models loaded** toggle, with a **Models loaded now** row listing them.
- **Show hidden** toggle at the bottom; when on, a **Hidden applications** dropdown lists the hidden apps
  by name. Hiding is a panel preference: a hidden app's override keeps applying when it runs.
- The selected app's section lists every Global option, each showing `Global (…)` when unset:
  Activator (Field + Record / Change / Clear, the same 15-second listening box as the global activator),
  Model (dropdown of every model, `not downloaded` marked, plus a **Model loaded** row and the note that
  the first dictation in this app waits a few seconds while it loads), Typing speed, Typing method,
  Spoken symbols, Press before typing, Press after typing, Rumble (Global / On / Off), Intensity
  (Global / 1..10, shown when rumble is on), Voice detector (Global / On / Off, note that Parakeet has
  no detector), **Hide this application**, and **Remove override** (press twice to confirm).
- A change that fails shows a `Field label="Note"` under the controls.

Backend callables (Decky `callable`, C3): `set_override_field(appid, field, value)` with field in
`ubermoc_apps.FIELDS` and value `""` (string), `""`, `true`/`false` or a number meaning Global;
`set_app_hidden(appid, hidden)`, `set_show_hidden(on)`, `set_keep_loaded(on)`, all of which return the
same override list the panel reads; `start_key_capture("activator", appid)` routes to
`start_capture(appid)` and stores the result on that app's override
(`purpose = "activator@<appid>"`, so the panel shows the countdown for that app and refreshes the
override list). `get_overrides` returns, per app, `activator`, `activator_label`, `activator_warnings`,
`model`, `model_installed`, `model_loaded`, `speed`, `method`, `rumble`, `rumble_level`, `vad`,
`hidden`, plus `global` (activator_label, model, speed, symbols, method, rumble, rumble_level, vad,
vad_supported), `running`, `hidden`, `show_hidden`, `keep_loaded`, `loaded_models`, `model`,
`model_is_global`, `model_loaded`.

## Open questions

1. Steam reports `MainRunningApp` for non-Steam shortcuts, but the appid used is Steam's. A device test
   should confirm a non-Steam app gets its override (the existing before/after overrides rely on the
   same feed, so this feature adds no new risk).
2. Proton games report the Steam appid, so they work the same way as installed games — needs a device check.
3. A game that starts while the panel is open reports its appid immediately; an app that starts while
   recording keeps that recording's activator and model, and the change applies to the next dictation.
4. Speed only changes the key delay/hold and the settle pauses; a game that samples input once per frame
   is the case Slow is for.
5. Decky panels have no real tabs: the selected app's section is a collapsible sub-section below the
   Applications dropdown.
6. Keeping two models loaded costs about 90 MB per model (measured, C13). Decky's memory limit for a
   plugin is not published; a device check should confirm both models stay resident.
