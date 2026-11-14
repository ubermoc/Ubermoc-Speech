#!/usr/bin/env python3
# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""End-to-end test of the voice test's backend with REAL servers and REAL models, without Decky and without a live microphone:
the recorder is replaced by pre-recorded clips (one WAV + one JSON per phrase, as record_clips.py wrote them).
  python3 scripts/voicetest_smoke.py <clips folder> name=<model file> [name=<model file> ...] [--condition noisy|quiet]
Runs the whole flow: plan, begin, record every phrase, run each model, score, verdict; then checks the recordings were deleted
and dictation is free again. Uses port 8199."""
import asyncio, importlib.util, json, logging, os, shutil, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = sys.argv[1:]
clips_dir = os.path.expanduser(args[0])
models = [a for a in args[1:] if "=" in a]
condition = args[args.index("--condition") + 1] if "--condition" in args else "noisy"
tmp = tempfile.mkdtemp()
decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR, decky.DECKY_USER_HOME = ROOT, tmp
decky.DECKY_PLUGIN_LOG_DIR, decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(tmp, "logs"), os.path.join(tmp, "settings")
decky.logger = logging.getLogger("vtsmoke")
async def _emit(*a, **k): return None
decky.emit = _emit
sys.modules["decky"] = decky
spec = importlib.util.spec_from_file_location("plugin_main", os.path.join(ROOT, "main.py"))
main = importlib.util.module_from_spec(spec); spec.loader.exec_module(main)
main.PORT = 8199
V, A, M = main.ubermoc_voicetest, main.ubermoc_audio, main.ubermoc_models
mdir = os.path.join(tmp, ".local", "share", "ubermoc-speech", "models"); os.makedirs(mdir)
# which recorded clip holds each phrase (matched on the text, ignoring spacing); the quiet or noisy take is chosen by the condition
norm = lambda t: " ".join(t.replace(",", " ").split()).lower()
by_text = {}
for f in sorted(os.listdir(clips_dir)):
    if f.endswith(".json"):
        m = json.load(open(os.path.join(clips_dir, f))); by_text.setdefault(norm(m["text"]), []).append((m.get("noisy", False), os.path.join(clips_dir, f[:-5] + ".wav")))
def clip_for(phrase):
    takes = by_text.get(norm(phrase[1]), [])
    want = condition == "noisy"
    return next((p for n, p in takes if n == want), takes[0][1] if takes else None)
def fake_record(path, stream_fn, on_level=None, cancel=None, **kw):
    i = int(os.path.basename(path)[:2]); src = clip_for(V.PHRASES[i])
    if on_level: on_level(1000.0, True)
    shutil.copy(src, path); return "finished", A.wav_stats(path)[0]
A.record_auto = fake_record

async def run():
    p = main.Plugin(); fails = 0
    def check(name, ok, detail=""):
        nonlocal fails; print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else "")); fails += (not ok)
    names = []
    for item in models:
        n, path = item.split("=", 1); names.append(n)
        os.symlink(os.path.expanduser(path), os.path.join(mdir, M.model_filename(n)))
    plan = await p.vt_plan(names)
    check("plan: phrases, free space and a level", plan["phrases"] == 22 and plan["free_mb"] > 0 and plan["plan"]["level"] in ("ok", "warn", "stop"), f"{plan['plan']['level']}: {plan['plan']['message'][:70]}")
    check("begin: rejects models that are not downloaded", not (await p.vt_begin(["parakeet-110m"], "noisy"))["ok"])
    b = await p.vt_begin(names, condition)
    check("begin: starts and blocks dictation", b["ok"] and p.phase == "voicetest", p.phase)
    check("dictation is refused during the test", (await p.start_recording())["ok"] is False)
    for i in range(len(V.PHRASES)):
        if clip_for(V.PHRASES[i]) is None: print(f"   (no recorded clip for phrase {i}: {V.PHRASES[i][1][:40]})"); continue
        r = await p.vt_record(i)
        if not r["ok"]: check(f"record phrase {i}", False, str(r))
    st = await p.vt_status()
    check("every phrase recorded", len(st["recorded"]) == sum(1 for ph in V.PHRASES if clip_for(ph)), f"{len(st['recorded'])} clips")
    tmpdir = p.vt["dir"]
    t0 = time.time(); r = await p.vt_run_start(); check("run starts", r["ok"])
    while True:
        st = await p.vt_status(); run = st["run"]
        if run and run["done"]: break
        await asyncio.sleep(0.5)
        if time.time() - t0 > 600: check("run finishes", False, "timeout"); break
    check("run finished without an error", not run["error"], run["error"])
    res = run["results"]
    print()
    for n in names:
        m = res["models"][n]; print(f"   {n:22} score {m['score']}%  groups {m['groups']}  median {m['median_s']}s  memory {m['memory_mb']} MB")
    v = res["verdict"]; print(f"\n   VERDICT: best = {v['best']}  too_close = {v['too_close']}\n   {v['reason']}\n")
    check("a verdict was reached", v["best"] in names)
    check("the recordings were deleted", not os.path.exists(tmpdir))
    check("dictation is free again", p.phase == "idle", p.phase)
    check("scores are saved for the panel", (await p.vt_plan(names))["saved"].get(condition, {}).get("verdict", {}).get("best") == v["best"])
    check("no speech server is left running", p.server_proc is None)
    await p.vt_finish()
    return fails

sys.exit(1 if asyncio.run(run()) else 0)
