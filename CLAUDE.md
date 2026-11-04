# Ubermoc Speech work process

Decky Loader plugin: hold an activator, speak, release, and the text from a local whisper.cpp server is typed into whatever has focus (gaming and desktop mode). TypeScript frontend in `src/`, Python backend `main.py` + `py_modules/`. Repo github.com/ubermoc/Ubermoc-Speech. Version lives in `VERSION`.

## Every change
1. Make the change.
2. Run the tests: `python3 -m unittest discover -s tests -v`.
3. Build: `./scripts/package.sh`.

## "Push" = full release
Bump `VERSION`, update README.md (the panel section lists every group and option) and any docs describing the change, commit as `vN: summary`, push `main`. A push to main triggers the GitHub Action that builds the zip and publishes the release (tag `v<VERSION>-<sha7>`, marked latest; tags are never reused). Confirm with `gh run list` and `gh release list`. The in-panel Update button installs from that latest release.
