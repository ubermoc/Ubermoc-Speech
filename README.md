# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin for the Steam Deck: hold a button
combo, talk, release, and the transcribed text is pasted into the running game.
Transcription is local, using a [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
server (Vulkan-enabled) on `127.0.0.1:8178`.

## Features
- Model manager: pick from a list (tiny to large, English or multilingual, full
  or compressed), download once, switch, delete. Models are kept in
  `~/.local/share/ubermoc-speech/models` and never re-downloaded.
- Hold-to-talk with any controller button combination (default: both right back
  buttons; record your own)
- Global "press before / after typing" keys (Enter, None, or any key with
  modifiers) with per-game and per-app overrides (installed Steam games and
  non-Steam shortcuts listed separately)
- Text delivery through the clipboard, then Ctrl+V (falls back to Steam typing it)
- Punctuation: automatic (Whisper) or spoken ("period", "comma", ...)
- Popup notification and light rumble while listening / transcribing
- Test section: see what was heard without typing it, send a test sentence,
  test the popup and rumble
- Update button that installs the latest GitHub release

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
