# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin that records from the microphone and
transcribes it to text with a local [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
server.

The panel opens as four collapsible groups; tap one to expand it:
- **Models**: Current model, Choose a model (download / delete)
- **Activator**: the hold-to-talk activator and the Typing settings
- **Testing**: record a test, see the text, test typing
- **Updates**: installs the latest GitHub release

## Activator (hold to talk)
Hold the activator to record, release to transcribe. It can be keyboard keys,
mouse buttons or controller buttons, alone or in any combination (for example
`Left Ctrl + F9`, `Mouse side button`, `R4 + R5`, or `Left Ctrl + R4`).

It is read by the plugin backend, not the panel, so it keeps working while a game
has focus. Reading is passive (nothing is sent to any device; the game and Steam
keep receiving all input).
- keyboards / mice / generic gamepads (Xbox, DualSense, Switch Pro, 8BitDo,
  handheld built-ins): `/dev/input/event*`
- Valve controllers: raw HID reports from `/dev/hidraw*` for the Steam Deck, the
  original Steam Controller (USB `1102`, dongle `1142`) and the 2026 Steam
  Controller (`1302`-`1305`, "Ibex" in the kernel). The Steam client stops
  delivering controller buttons to plugins while a game has focus, and the kernel
  removes the evdev gamepad while Steam holds the device, so raw reports are the
  way in. Every button table is cross-checked against the Linux `hid-steam` driver
  and SDL's drivers (see `tests/test_input.py`).
- The Steam interface's own controller feed is forwarded too. It works for any
  controller Steam supports, but only while the Steam interface is active, so it
  covers recording an activator and the Steam menu; the raw readers cover games.
- Valve's emulated keyboard / mouse are never used as activator inputs.

While an activator is being recorded, keyboards, mice and evdev gamepads (and
Valve's emulated keyboard/mouse) are held exclusively (EVIOCGRAB) so the system
does not react to the keys pressed; the hold lasts at most ~15 s. Controllers that
are read through raw HID cannot be held, so Steam still moves its menu with them.

## Typing (into whatever has focus)
When the activator is used, the transcription is typed into the focused field, in
gaming mode and desktop mode (for example Steam's search box or a browser address bar).

- **Virtual keyboard (default)**: the backend creates a virtual USB keyboard through
  the kernel's `uinput` interface and presses keys on it. Events enter below the display
  server, so they reach the focused window under gamescope and under KDE (X11 or
  Wayland) alike. InputPlumber and Steam Button Mapper rely on the same technique on
  SteamOS. The keyboard is created on first use and ignored by the activator reader,
  so typed text can never trigger the activator.
- **Steam keyboard (gaming mode only)**: asks the Steam interface to type through
  `SteamClient.Input.ControllerKeyboardSendText`, the call Steam's own on-screen
  keyboard uses for games. It falls back to the virtual keyboard if Steam does not answer.
- While the panel is open the text is only shown there (so testing never types into the
  menu). "Test typing" types a sentence after 5 seconds, wherever you click.
- Whisper annotations such as `[BLANK_AUDIO]` are dropped, typographic quotes/dashes are
  made plain, and line breaks are removed so nothing presses Enter.
- Typing waits until the activator's own keys are released, so a held Ctrl cannot turn
  it into shortcuts.
- Limits: it types by key position (assumes a US QWERTY layout) and only plain
  English characters; Steam's own keyboard shortcuts and games that read raw input
  instead of text may ignore it (try the Slow speed).

Settings (model, activator, typing) are written to two places, atomically and forced to
disk: Decky's settings folder (`~/homebrew/settings/Ubermoc Speech/`) and
`~/.local/share/ubermoc-speech/`. On load the newest copy of each setting wins and
a damaged file is kept as `settings.json.corrupt`. The plugin log records each save
and what was loaded (counts only, never which keys).

Privacy: devices are opened only while an activator is set or being recorded;
no key or button is logged, and only the activator you choose is stored.

Not supported as activators: analog sticks, analog triggers, trackpads, touch.

Tests: `python3 -m unittest discover -s tests -v`. Key names are generated from the
kernel header by `scripts/gen_codes.py`.

## Install
```
curl -L -o us.zip https://github.com/ubermoc/Ubermoc-Speech/releases/latest/download/Ubermoc-Speech.zip
sudo unzip -o us.zip -d ~/homebrew/plugins/
sudo systemctl restart plugin_loader
```
After that, use the Update button in the panel.

## Build
`./scripts/package.sh` builds `out/Ubermoc Speech.zip`. `bin/whisper-server`
(Vulkan, glibc >= 2.38) is committed; rebuild it with
`scripts/build-whisper-server.sh`. Every push to `main` publishes a release via
GitHub Actions. `VERSION` is the human-readable version.

Earlier experiments are in the git history and on the `experimental-v0.01` branch.
