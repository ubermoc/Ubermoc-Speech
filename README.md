# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin: hold an activator, speak, let go, and the
transcribed text is typed into whatever has focus, in gaming mode and desktop mode.
Transcription is local, with two engines to choose from: [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
(OpenAI Whisper models) and [parakeet.cpp](https://github.com/mudler/parakeet.cpp) (NVIDIA Parakeet models).

It also has a **Voice test**: pick the models you want to compare, read 22 short phrases (each recorded
automatically), and the plugin scores every model on your own voice and tells you which is best for you.

The panel opens as five collapsible groups (one open at a time), with the update
section always visible at the bottom:
- **Global options**: the activator, rumble (one buzz when recording starts, two when it stops, on any controller that can rumble; with a ten-step intensity slider; can be turned off), what is pressed around the typed text, and Typing settings (spoken symbols, method, speed)
- **Applications**: different before / after inputs for specific Steam and non-Steam apps
- **Models**: Current model, Choose a model (download / delete)
- **Voice test**: find the best model for your voice (see below)
- **Testing**: record a test, see the text, test typing
- **Updates** (always shown): installs the latest GitHub release

## Works together
Four projects made to be used together, so you can play World of Warcraft from the couch with only a controller:

- **[Ubermoc UI](https://github.com/ubermoc/Ubermoc-UI)** ([CurseForge](https://www.curseforge.com/wow/addons/ubermoc-ui)): the addon. It makes the game playable without a mouse or keyboard.
- **[Ubermoc Forever](https://github.com/ubermoc/Ubermoc-Forever)** ([CurseForge](https://www.curseforge.com/wow/addons/ubermoc-forever)): a default UI for World of Warcraft: Forever that works 100% with a controller, designed for the Steam Deck and Steam Controller.
- **[Ubermoc Warcraft Mod Updater](https://github.com/ubermoc/Ubermoc-Warcraft-Mod-Updater)**: a Decky plugin that installs Ubermoc UI the first time you open Midnight or Forever, and keeps it (and your other GitHub addons) updated from Gaming Mode.

Together: the Updater keeps the addons current, Ubermoc UI and Ubermoc Forever let you play, and Ubermoc Speech does the typing.

## Install
```
curl -L -o us.zip https://github.com/ubermoc/Ubermoc-Speech/releases/latest/download/Ubermoc-Speech.zip
sudo unzip -o us.zip -d ~/homebrew/plugins/
sudo systemctl restart plugin_loader
```
After that, use the Update button in the panel. It swaps the new files in without restarting Decky or Steam; then open Decky's plugin list (gear icon) and choose Reload for Ubermoc Speech to start using the new version.

## Models
Every model is available. Pick one, press Download, then select it under Current model. Models are kept in
`~/.local/share/ubermoc-speech/models` and never re-downloaded. Only a downloaded model can be current.
The panel lists the two recommended models first, then the rest. Each description gives the languages, a
relative accuracy tier, speed and memory in one line, and for the ten models we tested it adds our measured
results.

**Recommended** (from a comparison of ten English models on one speaker's recordings):

| Model | Engine | Download | Memory | Best for |
|---|---|---|---|---|
| `parakeet-tdt-0.6b-v2` (recommended) | NVIDIA Parakeet via [parakeet.cpp](https://github.com/mudler/parakeet.cpp) | 862 MB | about 1.3 GB | fewest mistakes, also with background noise |
| `small.en` (lighter option) | OpenAI Whisper via [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 466 MB | about 0.9 GB | very fast, good in quiet rooms, has the voice detector switch |

**Our voice test**: 52 clips from one English speaker with one noisy microphone (sentences, spelled letters and
digits, numbers like fifteen/fifty, sound-alike words such as two/to/too; 16 clips with background noise).
Share wrong, lower is better:

| Model | Sentences quiet / noisy | Spelled letters and digits quiet / noisy | Numbers | Sound-alikes | Time per phrase* |
|---|---|---|---|---|---|
| `parakeet-tdt-0.6b-v2` | 4.3% / 6.4% | 0.0% / 4.2% | 10.0% | 0.0% | 0.24 s |
| `small.en` | 5.5% / 11.5% | 0.0% / 4.2% | 10.0% | 0.0% | 0.06 s |
| `medium.en` | 9.8% / 9.0% | 0.0% / 4.2% | 10.0% | 0.0% | 0.14 s |
| `large-v3-turbo` and `large-v3-turbo-q8_0` | 5.5% / 9.0% | 8.2% / 4.2% | 5.6% | 3.2% | 0.10 s / 0.15 s |
| `large-v3` | 5.5% / 7.7% | 8.2% / 16.7% | 0.0% | 0.0% | 0.36 s |
| `parakeet-110m` | 8.6% / 7.7% | 14.8% / 20.8% | 2.8% | 3.2% | 0.05 s |
| `base.en` | 5.5% / 7.7% | 13.1% / 20.8% | 10.0% | 12.9% | 0.04 s |
| `tiny.en` | 7.4% / 10.3% | 3.3% / 25.0% | 66.7% | 6.5% | 0.02 s |
| `distil-large-v3.5` | 6.7% / 7.7% | 44.3% / 70.8% | 35.0% | 3.2% | 0.10 s |

\* on a desktop GPU; a slower processor makes every model slower. This is **one speaker with one microphone**, and
one clip moves a percentage by several points, so treat the numbers as a guide: a different voice, microphone or
room can change the order. Game names ("Orgrimmar", "Jaina") were the most common mistake for every model, and
numbers spoken in pairs ("eighteen, eighty") were sometimes written as years. The other models (all-language
versions, compressed `q5` versions and the rest) were not part of the test and say so.

- **Languages**: names ending in `.en` (and the distilled models) are English only and a little more accurate for
  English at the same size; the others handle many languages (about 99). `parakeet-tdt-0.6b-v3` covers 25
  European languages.
- **Accuracy** in the descriptions is a relative tier (basic, fair, good, very good, near-best, best), typical for
  that model family, plus the measured results where we have them.
- **Speed** is relative to the large model, from OpenAI's published table (for example turbo is about 8x faster).
- **Memory** for the standard models is whisper.cpp's published figure; "est." marks an estimate scaled from it
  for compressed (`q5`, `q8`) and distilled models.
- Compressed (`q5_0`, `q5_1`, `q8_0`) models are a fraction of the download and use less memory at a small cost
  in accuracy; `q8_0` is closer to the original than `q5_0`.
- **Voice detector**: the Silero detector stays a separate switch for Whisper models; it trims silence and
  background noise before the model hears the recording. Parakeet models have no such switch: they return empty
  text for silence by themselves, so the switch is off and does nothing for them.
- **Parakeet** (names starting `parakeet-`) runs on the GPU through Vulkan when one is available. NVIDIA's models
  are licensed CC-BY-4.0; see `THIRD_PARTY.md`.
- If the model you selected is no longer on the computer, the plugin falls back to a downloaded model (the
  recommended ones first); if none is downloaded, press Download on one.

## Voice test
Open the **Voice test** group. It walks you through:
1. An introduction: about 5 minutes, disk space, and privacy (recordings are deleted as soon as the scores are ready; only the scores are kept).
2. Choose the models to compare. Download sizes and your free space are shown, with stronger warnings on a handheld or when space is tight; downloading stops when less than 2 GB would remain.
3. A microphone check with a live level bar.
4. Read 22 phrases: normal sentences, spelled letters and digits, numbers, and words that sound alike. Each recording stops by itself when you stop talking.
5. Every chosen model transcribes your recordings and is scored on the words and letters it gets wrong. Speed and memory are measured on your device.
6. A table per model and "Best for your voice". A difference under 1.5 points is called a tie and the faster model is suggested. You can switch to the best model and delete the others.

The test assumes you play with background noise (that is how most people use it). If you can get somewhere quiet, tick "I am in a quiet place" or use "Test again in a quiet place" to compare. No AI model is needed to score: it is plain code comparing what you were asked to say with what each model heard.

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
