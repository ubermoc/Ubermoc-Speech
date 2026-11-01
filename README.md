# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin that records from the microphone and
transcribes it to text with a local [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
server.

Panel sections: Current model, Choose a model (download / delete), Activator,
Transcribe, Updates (installs the latest GitHub release).

## Activator (hold to talk)
Hold the activator to record, release to transcribe. It can be keyboard keys,
mouse buttons or controller buttons, alone or in any combination (for example
`Left Ctrl + F9`, `Mouse side button`, `R4 + R5`, or `Left Ctrl + R4`).

It is read by the plugin backend, not the panel, so it keeps working while a game
has focus. Reading is passive (nothing is grabbed or sent to any device).
- keyboards / mice / generic gamepads: `/dev/input/event*`
- Steam Deck built-in controller: raw HID reports from `/dev/hidraw*`
  (the Steam client stops delivering controller buttons to plugins while a game
  has focus, and the kernel removes the Deck's evdev gamepad while Steam holds
  the device). Button layout cross-checked against the Linux `hid-steam` driver
  and SDL's Steam Deck driver (see `tests/test_input.py`).
- Valve's emulated keyboard / mouse are ignored.

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
