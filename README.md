# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin: hold an activator, speak, let go, and the
transcribed text is typed into whatever has focus, in gaming mode and desktop mode.
Transcription is local, with two engines to choose from: [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
(OpenAI Whisper models) and [parakeet.cpp](https://github.com/mudler/parakeet.cpp) (NVIDIA Parakeet models).

The panel opens as four collapsible groups (one open at a time), with the update
section always visible at the bottom:
- **Global options**: the activator, rumble (one buzz when recording starts, two when it stops, on any controller that can rumble; with a ten-step intensity slider; can be turned off), what is pressed around the typed text, and Typing settings (spoken symbols, method, speed)
- **Applications**: different before / after inputs for specific Steam and non-Steam apps
- **Models**: Current model, Choose a model (download / delete)
- **Testing**: record a test, see the text, test typing
- **Updates** (always shown): installs the latest GitHub release

## Works together
Three projects made to be used together, so you can play World of Warcraft from the couch with only a controller:

- **[Ubermoc UI](https://github.com/ubermoc/Ubermoc-UI)** ([CurseForge](https://www.curseforge.com/wow/addons/ubermoc-ui)): the addon. It makes the game playable without a mouse or keyboard.
- **[Ubermoc Warcraft Mod Updater](https://github.com/ubermoc/Ubermoc-Warcraft-Mod-Updater)**: a Decky plugin that installs Ubermoc UI the first time you open Midnight or Forever, and keeps it (and your other GitHub addons) updated from Gaming Mode.

Together: the Updater keeps the addon current, Ubermoc UI lets you play, and Ubermoc Speech does the typing.

## Install
```
curl -L -o us.zip https://github.com/ubermoc/Ubermoc-Speech/releases/latest/download/Ubermoc-Speech.zip
sudo unzip -o us.zip -d ~/homebrew/plugins/
sudo systemctl restart plugin_loader
```
After that, use the Update button in the panel. It swaps the new files in without restarting Decky or Steam; then open Decky's plugin list (gear icon) and choose Reload for Ubermoc Speech to start using the new version.

## Models
Two models are offered. Pick one, press Download, then select it under Current model. Models are kept in
`~/.local/share/ubermoc-speech/models` and never re-downloaded. Only a downloaded model can be current.

| Model | Engine | Download | Memory | Best for |
|---|---|---|---|---|
| `parakeet-tdt-0.6b-v2` (recommended) | NVIDIA Parakeet via [parakeet.cpp](https://github.com/mudler/parakeet.cpp) | 862 MB | about 1.3 GB | fewest mistakes, also with background noise |
| `small.en` (lighter) | OpenAI Whisper via [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 466 MB | about 0.9 GB | very fast, good in quiet rooms, has the voice detector switch |

Both are English only. They were chosen from a comparison of ten English models on one speaker's recordings
(52 clips: sentences, spelled letters and digits, numbers like fifteen/fifty, sound-alike words such as
two/to/too; 16 of the clips with background noise):

| Words wrong | `parakeet-tdt-0.6b-v2` | `small.en` |
|---|---|---|
| Sentences, quiet / noisy | 4.3% / 6.4% | 5.5% / 11.5% |
| Spelled letters and digits, quiet / noisy | 0.0% / 4.2% | 0.0% / 4.2% |
| Numbers like fifteen/fifty | 10.0% | 10.0% |
| Sound-alike words | 0.0% | 0.0% |
| Time per phrase on a desktop GPU | about 0.24 s | about 0.06 s |

This is **one speaker with one microphone**, and one clip moves a percentage by several points, so treat the
numbers as a guide: a different voice, microphone or room can change the order. Names from games ("Orgrimmar",
"Jaina") were the most common mistake for every model, and numbers spoken in pairs ("eighteen, eighty") were
sometimes written as years.

- **Voice detector**: the Silero detector stays a separate switch for Whisper models; it trims silence and
  background noise before the model hears the recording. Parakeet has no such switch: it returns empty text for
  silence by itself, so the switch is off and does nothing for it.
- **Parakeet** runs on the GPU through Vulkan when one is available. NVIDIA's models are licensed CC-BY-4.0;
  see `THIRD_PARTY.md`.
- **Older models**: versions before 0.41 offered 23 models. They are no longer offered for download, but a model
  that is already on your computer keeps working, stays selected, and is listed (marked as an older option) so you
  can delete it. If the model you had selected is gone, the plugin falls back to a recommended model that is
  downloaded; if none is, press Download on one.

## Activator (hold to talk)
Hold the activator to record, release to transcribe. It can be keyboard keys, mouse
buttons or controller buttons, alone or in any combination (`Left Ctrl + F9`,
`Mouse side button`, `R4 + R5`, `Left Ctrl + R4`).

It is read by the plugin backend, not the panel, so it keeps working while a game has
focus. Reading is passive (nothing is sent to any device; the game and Steam keep
receiving all input).
- keyboards / mice / generic gamepads (Xbox, DualSense, Switch Pro, 8BitDo, handheld
  built-ins): `/dev/input/event*`
- Valve controllers: raw HID reports from `/dev/hidraw*` for the Steam Deck, the original
  Steam Controller (USB `1102`, dongle `1142`) and the 2026 Steam Controller (`1302`-`1305`,
  "Ibex" in the kernel). The Steam client stops delivering controller buttons to plugins
  while a game has focus, and the kernel removes the evdev gamepad while Steam holds the
  device, so raw reports are the way in. Every button table is cross-checked against the
  Linux `hid-steam` driver and SDL's drivers (see `tests/test_input.py`).
- The Steam interface's own controller feed is forwarded too. It works for any controller
  Steam supports, but only while the Steam interface is active, so it covers recording and
  the Steam menu; the raw readers cover games.
- Valve's emulated keyboard / mouse are never used as activator inputs.

While an input is being recorded, keyboards, mice and evdev gamepads (and Valve's emulated
keyboard/mouse) are held exclusively (EVIOCGRAB) so the system does not react to what is
pressed; the hold lasts at most ~15 s. Controllers read through raw HID cannot be held by
the plugin, so the menu instead shows a "listening" box that takes focus and consumes every
controller press (`stopPropagation()` + `preventDefault()` in `onButtonDown`, `true` from
`onGamepadDirection`: the same calls Valve's own interface code makes), so a press cannot
click Cancel, move the menu or close it. It has no button a controller could press; touch or
mouse cancels it, and it stops by itself after 15 s.

The button you tapped to start recording is ignored when it is only tapped (so the tap does
not record itself), but it counts when you press it again, or keep it held for 0.6 s.

Privacy: devices are opened only while an activator is set or being recorded; no key or
button is logged, and only the inputs you choose are stored.

Not supported as activators: analog sticks, analog triggers, trackpads, touch.

## Typing
When the activator is used, the transcription is typed into the focused field.

- **Virtual keyboard (default)**: the backend creates a virtual USB keyboard through the
  kernel's `uinput` interface and presses keys on it. The events enter below the display
  server, so they reach the focused window under gamescope and under KDE (X11 or Wayland)
  alike. InputPlumber and Steam Button Mapper rely on the same technique on SteamOS.
- **Steam keyboard (gaming mode only)**: asks the Steam interface to type through
  `SteamClient.Input.ControllerKeyboardSendText`, the call Steam's own on-screen keyboard
  uses for games. It falls back to the virtual keyboard if Steam does not answer.
- **Spoken symbols**: by default, saying "backslash" types the word. Set it to *Type the symbol* and it types `\`. Recognised names: back slash, forward slash, slash, underscore, at sign, hyphen, tilde, caret, open/close (or left/right) parenthesis, bracket and brace, semi colon, ampersand, asterisk, plus sign, equals sign, percent sign, dollar sign, hash tag, hash sign, pound sign, vertical bar, back tick, less than sign, greater than sign. Slashes, underscore, at sign, hyphen, tilde and caret join the neighbouring words (`C backslash users` becomes `C\users`). Plain words such as dot, period, comma, colon, pipe or star are left alone because they are ordinary English; Whisper already adds sentence punctuation.
- While the panel is open the text is only shown there (so testing never types into the
  menu). "Test typing" types a sentence after 5 seconds, wherever you click.
- Whisper annotations such as `[BLANK_AUDIO]` are dropped, typographic quotes/dashes are
  made plain, and line breaks are removed so nothing presses Enter.
- Typing waits until the activator's own inputs are released, so a held Ctrl cannot turn
  it into shortcuts. The plugin's virtual devices are ignored by the activator reader, so
  typed text can never trigger it.
- Limits: typing is by key position (assumes a US QWERTY layout) and plain English
  characters only; games that read raw input instead of text may ignore it (try Slow).

### Input around the text
"Press before typing" and "Press after typing" each offer **None**, **Enter** (the default
for both) or **Any key or combination**, recorded exactly like the activator: hold keyboard
keys, mouse buttons and/or controller buttons together, then let go. The plugin presses what
you recorded on virtual devices: a keyboard, a mouse, and a gamepad that copies the Xbox 360
controller (`045e:028e`, the identity Steam Button Mapper uses for a pad that is recognised
in games).

- The gamepad declares its buttons in the same order as a real Xbox 360 pad and nothing
  numbered below them: SDL numbers buttons by ascending code from 0x120, so an extra
  low-numbered button would shift every game's controller layout. L2/R2 are axes as on the
  real pad. The Deck's R4/R5/L4/L5 use the kernel's standard grip codes; QAM and the pad
  clicks use extra buttons. Most games and Steam do not respond to those, and the panel says
  so when you record one.
- Combinations are pressed the documented way (ydotool: `29:1 56:1 59:1 59:0 56:0 29:0`):
  down in order, held, released in reverse.
- They are held much longer than typed letters (30/60/120 ms for Fast/Normal/Slow) because
  programs that sample input once per frame miss very short presses (xdotool's default is only
  about 6-12 ms). A pause after the "before" input (0.12/0.25/0.5 s) gives a chat box time to
  open. Nothing is pressed when there is nothing to type.

### Per-application overrides
The **Applications** group has a *Steam applications* list (installed games) and a *Non-Steam
applications* list (shortcuts in your Steam library). Choosing one adds an override for it,
starting from a copy of the Global options' before / after and Spoken symbols; edit them with the same None /
Enter / Any key or combination controls, or remove the override to go back to Global options. Each override also has its own *Spoken symbols* choice (type the word or the symbol); an override saved before this existed follows the Global option.

The running game is read from the Steam interface (`Router.MainRunningApp`) and reported to
the backend when Steam announces an app starting or stopping, and each time you start dictating (no polling). The running app's override is used if
it has one, otherwise Global options. This works in
gaming mode, where Steam knows what is running; in desktop mode (no Steam interface) Global
options always apply. The non-Steam list is whatever Steam has as shortcut-type entries, so
it can include things like remote-play shortcuts.

## Settings
Model, activator, typing and before/after inputs are written to two places, atomically and
forced to disk: Decky's settings folder (`~/homebrew/settings/Ubermoc Speech/`) and
`~/.local/share/ubermoc-speech/`. On load the newest copy of each setting wins and a damaged
file is kept as `settings.json.corrupt`. The plugin log records each save and what was loaded
(counts only, never which inputs).

## Build
`./scripts/package.sh` builds `out/Ubermoc Speech.zip`. `bin/whisper-server` (Vulkan,
glibc >= 2.38) is committed; rebuild it with `scripts/build-whisper-server.sh`.
`bin/parakeet-server` (Vulkan, glibc >= 2.34) is committed too; it comes from a pinned parakeet.cpp
release, checked against a recorded SHA-256, by `scripts/fetch-parakeet-server.sh`.
`scripts/integration_smoke.py` runs the plugin's own code against the real servers and models. Every push to
`main` publishes a release via GitHub Actions. `VERSION` is the human-readable version.
Tests: `python3 -m unittest discover -s tests -v`. Key names are generated from the kernel
header by `scripts/gen_codes.py`.

Earlier experiments are in the git history and on the `experimental-v0.01` branch.

## Support the project
Ubermoc Speech is free. If you'd like to say thanks, donations are optional and always appreciated:

- [GitHub Sponsors](https://github.com/sponsors/ubermoc)
- [Ko-fi](https://ko-fi.com/ubermoc)
- [Buy Me a Coffee](https://buymeacoffee.com/Ubermoc)

Made by Marcos ([Ubermoc](https://github.com/ubermoc)).

## Licence
Copyright (c) 2026 ubermoc. All rights reserved. This is **not** open source: you may install
and use the official release, and read the code, but copying, modifying, redistributing, or
using it to train or prompt AI systems is not allowed. See `LICENSE`. Third-party components
(whisper.cpp and others) keep their own licences, see `THIRD_PARTY.md`. Versions before 0.23
were published under the MIT licence.
