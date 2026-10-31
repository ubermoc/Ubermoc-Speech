# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin for the Steam Deck: hold a button
combo, talk, release, and the transcribed text is pasted into the running game.
Transcription is local, using a [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
server (Vulkan-enabled) on `127.0.0.1:8178`.

## Features
- Model manager (download/delete `tiny`, `base`, `small`, English-only or multilingual)
- Hold-to-talk with a configurable button combo (default R4 + R5; record your own)
- Per-game profiles that override the global defaults
- Text delivery through the clipboard, then Ctrl+V
- Configurable key pressed *before* and *after* the text (None / Enter / Custom,
  recorded by pressing the key or combo in the panel)

## Install
Build the zip and copy it to the Deck, then use Decky's developer
"Install plugin from ZIP":

```bash
./scripts/package.sh        # -> out/Ubermoc Speech.zip
```

`bin/whisper-server` is committed (built on Ubuntu 24.04, needs glibc >= 2.38 and
libstdc++ >= 13, so SteamOS 3.6+). Rebuild it with `scripts/build-whisper-server.sh`.

## Status: not yet tested on a Deck
Written and compiled, but these need validation on real hardware:
- Controller button bitmasks (`src/buttons.ts` constants are from memory; the
  "record combo" feature stores observed masks, so use it if R4+R5 does nothing)
- Key presses via `SteamClient.Input.ControllerKeyboardSetKeyState` and HID codes
- Clipboard handoff to games (`wl-copy` -> `xclip` -> `xsel` -> panel clipboard)
- Microphone capture through `parecord`/`pw-record` as root from Decky
- End-to-end transcription (the server binary starts, but no model could be
  downloaded in the build environment to test inference)
