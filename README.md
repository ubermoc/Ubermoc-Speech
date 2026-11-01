# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin that records from the microphone and
transcribes it to text with a local [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
server.

The panel opens as three collapsible groups (one open at a time) with the update
section always visible at the bottom:
- **Models**: Current model, Choose a model (download / delete)
- **Activator**: the hold-to-talk activator, the keys pressed around the typed text,
  and the Typing settings
- **Testing**: record a test, see the text, test typing
- **Updates** (always shown): installs the latest GitHub release

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
