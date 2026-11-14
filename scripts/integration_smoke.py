#!/usr/bin/env python3
# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""End-to-end smoke test of the plugin's own code with the REAL server binaries and REAL models, without Decky.
Not part of the unit tests (it needs bin/, model files and a few seconds of CPU/GPU).
  python3 scripts/integration_smoke.py <name>=<model file> [...] --speech jfk.wav [--silence quiet.wav]
Example: scripts/integration_smoke.py parakeet-110m=/tmp/m.gguf base.en=~/.local/share/ubermoc-speech/models/ggml-base.en.bin --speech jfk.wav
For each model: start the server the way the plugin does, transcribe the speech clip and the silence clip, stop the server.
Uses port 8199 so it never meets a running plugin."""
import asyncio, importlib.util, logging, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = sys.argv[1:]
speech = args[args.index("--speech") + 1]
silence = args[args.index("--silence") + 1] if "--silence" in args else ""
models = [a for a in args if "=" in a]
tmp = tempfile.mkdtemp()
decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR, decky.DECKY_USER_HOME = ROOT, tmp
decky.DECKY_PLUGIN_LOG_DIR, decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(tmp, "logs"), os.path.join(tmp, "settings")
decky.logger = logging.getLogger("smoke")
async def _emit(*a, **k): return None
decky.emit = _emit
sys.modules["decky"] = decky
spec = importlib.util.spec_from_file_location("plugin_main", os.path.join(ROOT, "main.py"))
main = importlib.util.module_from_spec(spec); spec.loader.exec_module(main)
main.PORT = 8199
mdir = os.path.join(tmp, ".local", "share", "ubermoc-speech", "models"); os.makedirs(mdir)

async def run():
    bad = 0
    for item in models:
        name, path = item.split("=", 1)
        p = main.Plugin()
        os.symlink(os.path.expanduser(path), os.path.join(mdir, main.ubermoc_models.model_filename(name)))
        p._save_settings(model=name)
        sel = p._selected()
        t0 = time.time(); r = await main.Plugin.start_server(p); load = time.time() - t0
        if sel != name or not r.get("ok"):
            print(f"FAIL {name}: selected={sel!r} start={r}"); bad += 1; await main.Plugin.stop_server(p); continue
        out = {}
        for label, clip in (("speech", speech), ("silence", silence)):
            if not clip: continue
            t1 = time.time(); text = await asyncio.get_event_loop().run_in_executor(None, p._transcribe, clip); out[label] = (text, time.time() - t1)
        ok = "ask not what your country can do for you" in out["speech"][0].lower() and (not silence or not out["silence"][0].strip(" .[]()_-").strip())
        print(f"{'ok  ' if ok else 'FAIL'} {name:22} engine={main.ubermoc_models.engine_of(name):8} load {load:4.1f}s | speech {out['speech'][1]:.2f}s: {out['speech'][0][:90]!r}" + (f" | silence {out['silence'][1]:.2f}s: {out['silence'][0]!r}" if silence else ""))
        bad += (not ok)
        await main.Plugin.stop_server(p)
        os.remove(os.path.join(mdir, main.ubermoc_models.model_filename(name)))
    return bad

sys.exit(1 if asyncio.run(run()) else 0)
