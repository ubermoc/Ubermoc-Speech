# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin for the Steam Deck: hold a button
combo, talk, release, and the transcribed text is pasted into the running game.
Transcription is local, using a [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
server (Vulkan-enabled) on `127.0.0.1:8178`.

## Features
- Model list: tap a model, then Download or Delete. Models are kept in
  `~/.local/share/ubermoc-speech/models` and never re-downloaded.
- Hold-to-talk with any controller button combination (default: both right back
  buttons; tap to record your own)
- "Press before / after typing" keys: Enter, None, or a key / combination
- Per-game profile toggle (its own buttons and keys for the running game)
- Text delivery through the clipboard, then Ctrl+V (falls back to Steam typing it)
- Test section: shows what was heard instead of typing it
- Update button that installs the latest GitHub release

Extra features (spoken punctuation, popup + rumble, installed-game lists,
key-press test) are kept on the `experimental-v0.01` branch.

## Install
Build the zip and copy it to the Deck, then use Decky's developer
"Install plugin from ZIP":

```bash
./scripts/package.sh        # -> out/Ubermoc Speech.zip
```

`bin/whisper-server` is committed (built on Ubuntu 24.04, needs glibc >= 2.38 and
libstdc++ >= 13, so SteamOS 3.6+). Rebuild it with `scripts/build-whisper-server.sh`.

## Status: needs testing on real hardware
The panel logic is exercised in a headless browser with stand-ins for Decky and
Steam, but these still need validation on a real system:
- Steam button ids and the three input feeds (use "Live input" to see them)
- Key presses via `SteamClient.Input.ControllerKeyboardSetKeyState`
- Clipboard handoff to games (`wl-copy` -> `xclip` -> `xsel` -> panel clipboard
  -> Steam text entry)
- Microphone capture through `parecord` / `pw-record` as root from Decky
- Rumble (Gamepad vibration actuator and Steam haptic pulses)
- Listing of installed Steam games and non-Steam shortcuts
- End-to-end transcription (the server binary starts, but no model could be
  downloaded in the build environment to test inference)

Versioning: `VERSION` holds the human-readable version (bump it when releasing);
each build also records its commit in `BUILD`.
