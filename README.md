# Ubermoc Speech

A [Decky Loader](https://decky.xyz) plugin that records from the microphone and
transcribes it to text with a local [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
server.

Current state: a fresh start. The panel has a Transcribe section (download the
model once, record, see the text) and an Updates section that installs the
latest GitHub release.

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
