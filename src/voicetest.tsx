// Copyright (c) 2026 ubermoc. All rights reserved.
// Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
// train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
// except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
import { callable } from "@decky/api";
import { ButtonItem, Field, PanelSection, PanelSectionRow, ProgressBarWithInfo, ToggleField } from "@decky/ui";
import { useEffect, useState } from "react";

// The voice test: pick models, download them, read 22 phrases (each is recorded and stops by itself), then every model is scored on
// those recordings and the screen says which is best for THIS voice on THIS device. All the logic lives in the backend
// (py_modules/ubermoc_voicetest.py); this file only draws the steps.

interface Mdl { name: string; size_mb: number; description: string; installed: boolean }
interface Plan { phrases: number; minutes: number; handheld: boolean; free_mb: number; recorder: boolean; saved: Record<string, Saved>; plan: { download_mb: number; free_mb: number; level: "ok" | "warn" | "stop"; message: string } }
interface ModelResult { score: number | null; groups: Record<string, number>; median_s: number; memory_mb?: number; size_mb?: number; error?: string }
interface Verdict { best: string | null; close: string[]; too_close: boolean; reason: string; ranking: string[] }
interface Saved { models: Record<string, ModelResult>; verdict: Verdict; when: number; phrases: number }
interface Status { active: boolean; level: { db: number; speaking: boolean }; recorded: number[]; run: Run | null; condition: string; phase: string }
interface Run { model: string; index: number; total: number; clip: number; clips: number; done: boolean; error: string; results: Saved | null }
interface Phrase { ok: boolean; i: number; total: number; kind: string; text: string; hint: string }
interface DlProgress { done: number; total: number }

const vtPlan = callable<[string[]], Plan>("vt_plan");
const vtBegin = callable<[string[], string], { ok: boolean; error?: string; total?: number }>("vt_begin");
const vtMicCheck = callable<[], { ok: boolean; error?: string; peak?: number; kind?: string; message?: string }>("vt_mic_check");
const vtPhrase = callable<[number], Phrase>("vt_phrase");
const vtRecord = callable<[number], { ok: boolean; error?: string; reason?: string; seconds?: number; peak?: number; warn?: string }>("vt_record");
const vtStatus = callable<[], Status>("vt_status");
const vtRunStart = callable<[], { ok: boolean; error?: string }>("vt_run_start");
const vtCancel = callable<[], { ok: boolean }>("vt_cancel");
const vtFinish = callable<[], { ok: boolean }>("vt_finish");
const dlModel = callable<[string], { ok: boolean; error?: string }>("download_model");
const dlProgress = callable<[], Record<string, DlProgress>>("get_download_progress");
const dlCancel = callable<[string], boolean>("cancel_download");
const useModel = callable<[string], { ok: boolean; error?: string }>("set_model");
const dropModel = callable<[string], { ok: boolean; error?: string }>("delete_model");

type Stage = "intro" | "models" | "download" | "mic" | "phrases" | "running" | "results";
const RECOMMENDED = ["parakeet-tdt-0.6b-v2", "small.en"];

// The screen's memory survives the panel being closed and opened again (the quick-access menu closes often).
const mem: { stage: Stage; picked: Record<string, boolean>; quiet: boolean; idx: number; results: Saved | null; condition: string } =
  { stage: "intro", picked: {}, quiet: false, idx: 0, results: null, condition: "noisy" };

const sizeLabel = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);
const levelPct = (db: number) => Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
const pct = (v: number | undefined) => (v === undefined ? "-" : `${Math.round(v * 10) / 10}%`);
const when = (t: number) => new Date(t * 1000).toLocaleDateString();

export function VoiceTest(props: { models: Mdl[]; refresh: () => Promise<void>; active: string }) {
  const [stage, setStageState] = useState<Stage>(mem.stage);
  const [picked, setPickedState] = useState<Record<string, boolean>>(
    Object.keys(mem.picked).length ? mem.picked : Object.fromEntries(RECOMMENDED.map((n) => [n, true])));
  const [quiet, setQuietState] = useState<boolean>(mem.quiet);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [dl, setDl] = useState<Record<string, DlProgress>>({});
  const [dlError, setDlError] = useState("");
  const [micBusy, setMicBusy] = useState(false);
  const [mic, setMic] = useState<{ kind: string; message: string } | null>(null);
  const [level, setLevel] = useState(-90);
  const [idx, setIdxState] = useState<number>(mem.idx);
  const [phrase, setPhrase] = useState<Phrase | null>(null);
  const [recording, setRecording] = useState(false);
  const [recMsg, setRecMsg] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [results, setResultsState] = useState<Saved | null>(mem.results);
  const [condition, setCondition] = useState<string>(mem.condition);
  const [info, setInfo] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const setStage = (s: Stage) => { mem.stage = s; setStageState(s); setInfo(""); };
  const setPicked = (p: Record<string, boolean>) => { mem.picked = p; setPickedState(p); };
  const setQuiet = (q: boolean) => { mem.quiet = q; setQuietState(q); };
  const setIdx = (i: number) => { mem.idx = i; setIdxState(i); };
  const setResults = (r: Saved | null) => { mem.results = r; setResultsState(r); };

  const names = props.models.filter((m) => picked[m.name]).map((m) => m.name);
  const toDownload = props.models.filter((m) => picked[m.name] && !m.installed);

  // On opening: if a test was running in the backend and is gone (plugin reloaded), start from the top.
  useEffect(() => {
    void vtStatus().then((s) => {
      if (!s.active && (mem.stage === "phrases" || mem.stage === "running")) setStage("intro");
    }).catch(() => undefined);
    void vtPlan(names).then(setPlan).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The space check follows the choice of models.
  useEffect(() => {
    if (stage === "models") void vtPlan(names).then(setPlan).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, names.join(",")]);

  // Live level bar while the microphone is being checked or a phrase is recorded.
  useEffect(() => {
    if (!(micBusy || recording)) return;
    const t = setInterval(() => { void vtStatus().then((s) => setLevel(s.level.db)).catch(() => undefined); }, 150);
    return () => clearInterval(t);
  }, [micBusy, recording]);

  // Downloads, one after the other.
  useEffect(() => {
    if (stage !== "download") return;
    let stop = false;
    const poll = setInterval(() => { void dlProgress().then(setDl).catch(() => undefined); }, 500);
    (async () => {
      setDlError("");
      for (const m of toDownload) {
        if (stop) return;
        const r = await dlModel(m.name);
        if (!r.ok) { setDlError(`${m.name}: ${r.error ?? "download failed"}`); return; }
      }
      await props.refresh();
      if (!stop) setStage("mic");
    })().catch((e) => setDlError(String(e)));
    return () => { stop = true; clearInterval(poll); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  // The phrase on screen.
  useEffect(() => {
    if (stage !== "phrases") return;
    void vtPhrase(idx).then(setPhrase).catch(() => undefined);
  }, [stage, idx]);

  // The scoring run.
  useEffect(() => {
    if (stage !== "running") return;
    let stop = false;
    void vtRunStart().then((r) => { if (!r.ok && !stop) setInfo(r.error ?? "could not start"); });
    const t = setInterval(() => {
      void vtStatus().then((s) => {
        if (stop || !s.run) return;
        setRun(s.run);
        if (s.run.done) {
          if (s.run.error) { setInfo(s.run.error); return; }
          if (s.run.results) { setResults(s.run.results); mem.condition = s.condition; setCondition(s.condition); setStage("results"); }
        }
      }).catch(() => undefined);
    }, 500);
    return () => { stop = true; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  const checkMic = async () => {
    setMicBusy(true); setMic(null); setLevel(-90);
    try {
      const r = await vtMicCheck();
      setMic(r.ok ? { kind: r.kind ?? "", message: r.message ?? "" } : { kind: "nothing", message: r.error ?? "The microphone could not be read." });
    } finally { setMicBusy(false); }
  };

  const begin = async () => {
    const r = await vtBegin(names, quiet ? "quiet" : "noisy");
    if (!r.ok) { setMic({ kind: "nothing", message: r.error ?? "Could not start the test." }); return; }
    setIdx(0); setRecMsg(""); setStage("phrases");
  };

  const record = async () => {
    setRecording(true); setRecMsg(""); setLevel(-90);
    try {
      const r = await vtRecord(idx);
      if (r.ok) {
        const total = phrase?.total ?? 22;
        if (idx + 1 >= total) setStage("running"); else setIdx(idx + 1);
      } else setRecMsg(r.warn || r.error || "That did not work. Try again.");
    } finally { setRecording(false); }
  };

  const leave = async () => { await vtFinish(); setRun(null); setResults(null); setStage("intro"); };

  const best = results?.verdict.best ?? null;
  const others = results ? Object.keys(results.models).filter((n) => n !== best) : [];

  // ---------------- the screens ----------------
  if (stage === "intro") {
    const saved = plan?.saved ?? {};
    return (
      <PanelSection title="Find the best model for your voice">
        <PanelSectionRow><Field label="What this does" description={`You read ${plan?.phrases ?? 22} short phrases. Each model you pick listens to your recordings and is scored on the words and letters it gets wrong. At the end it tells you which model is best for your voice. It takes about ${plan?.minutes ?? 5} minutes.`} /></PanelSectionRow>
        <PanelSectionRow><Field label="Your voice stays private" description="Your voice is recorded only for the test and the recordings are deleted as soon as the scores are ready. Only the scores are kept." /></PanelSectionRow>
        <PanelSectionRow><Field label="Disk space" description={`Models are downloaded for the test, between about 0.1 and 3 GB each. The next screen shows how much space you have${plan?.handheld ? " (this looks like a handheld, so be selective)" : ""}. You can delete the models you do not keep when the test ends.`} /></PanelSectionRow>
        <PanelSectionRow><Field label="Background noise" description="The test assumes you play with your usual background noise on (game sound, a fan, music), because that is how you will really use it. If you can get somewhere quiet you can also run it a second time and compare." /></PanelSectionRow>
        {Object.entries(saved).map(([cond, s]) => (
          <PanelSectionRow key={cond}><Field label={`Last test (${cond === "quiet" ? "quiet place" : "with background noise"})`} description={`${s.verdict.best ?? "no result"} - ${when(s.when)}`} /></PanelSectionRow>
        ))}
        {plan && !plan.recorder && <PanelSectionRow><Field label="Cannot record" description="No recording program (parec/parecord) was found, so the test cannot run on this device." /></PanelSectionRow>}
        <PanelSectionRow><ButtonItem layout="below" disabled={!!plan && !plan.recorder} onClick={() => setStage("models")}>Start</ButtonItem></PanelSectionRow>
      </PanelSection>
    );
  }

  if (stage === "models") {
    const level2 = plan?.plan.level ?? "ok";
    return (
      <>
        <PanelSection title="Models to test">
          <PanelSectionRow><Field label={level2 === "ok" ? "Space" : level2 === "warn" ? "Check your space" : "Not enough space"} description={plan?.plan.message ?? "Checking the free space..."} /></PanelSectionRow>
          {props.models.map((m) => (
            <PanelSectionRow key={m.name}>
              <ToggleField label={m.name} description={`${sizeLabel(m.size_mb)} - ${m.installed ? "already downloaded" : "needs a download"} - ${m.description.slice(0, 120)}${m.description.length > 120 ? "..." : ""}`}
                checked={!!picked[m.name]} onChange={(on: boolean) => setPicked({ ...picked, [m.name]: on })} />
            </PanelSectionRow>
          ))}
        </PanelSection>
        <PanelSection>
          <PanelSectionRow><ButtonItem layout="below" disabled={names.length === 0 || level2 === "stop"} onClick={() => setStage(toDownload.length ? "download" : "mic")}>
            {names.length === 0 ? "Pick at least one model" : toDownload.length ? `Download ${toDownload.length} and continue` : `Continue with ${names.length}`}</ButtonItem></PanelSectionRow>
          <PanelSectionRow><ButtonItem layout="below" onClick={() => setStage("intro")}>Back</ButtonItem></PanelSectionRow>
        </PanelSection>
      </>
    );
  }

  if (stage === "download") {
    return (
      <PanelSection title="Downloading">
        {toDownload.map((m) => {
          const p = dl[m.name];
          return p ? (
            <PanelSectionRow key={m.name}><ProgressBarWithInfo label={m.name} nProgress={p.total ? Math.min(100, (p.done / p.total) * 100) : 0} sOperationText={`${Math.round(p.done / 1e6)} of ${Math.round(p.total / 1e6)} MB`} /></PanelSectionRow>
          ) : (
            <PanelSectionRow key={m.name}><Field label={m.name} description="Waiting..." /></PanelSectionRow>
          );
        })}
        {dlError && <PanelSectionRow><Field label="Download problem" description={dlError} /></PanelSectionRow>}
        <PanelSectionRow><ButtonItem layout="below" onClick={() => { toDownload.forEach((m) => void dlCancel(m.name)); setStage("models"); }}>{dlError ? "Back" : "Cancel"}</ButtonItem></PanelSectionRow>
      </PanelSection>
    );
  }

  if (stage === "mic") {
    const usable = mic && mic.kind !== "nothing";
    return (
      <PanelSection title="Check your microphone">
        <PanelSectionRow><Field label="Say it when the bar moves" description={'Press the button, then say "testing one two three" at your normal volume for five seconds.'} /></PanelSectionRow>
        <PanelSectionRow><ToggleField label="I am in a quiet place" description="Leave this off if you play with background noise, which the test assumes. Turn it on only if you moved somewhere quiet." checked={quiet} onChange={(q: boolean) => setQuiet(q)} /></PanelSectionRow>
        {micBusy && <PanelSectionRow><ProgressBarWithInfo label="Level" nProgress={levelPct(level)} sOperationText={`${Math.round(level)} dB`} /></PanelSectionRow>}
        {mic && <PanelSectionRow><Field label={mic.kind === "good" ? "Good" : mic.kind === "nothing" ? "Nothing heard" : "Check the level"} description={mic.message} /></PanelSectionRow>}
        <PanelSectionRow><ButtonItem layout="below" disabled={micBusy} onClick={() => void checkMic()}>{micBusy ? "Listening..." : mic ? "Check again" : "Check microphone"}</ButtonItem></PanelSectionRow>
        {usable && <PanelSectionRow><ButtonItem layout="below" onClick={() => void begin()}>Start the phrases</ButtonItem></PanelSectionRow>}
        <PanelSectionRow><ButtonItem layout="below" onClick={() => setStage("models")}>Back</ButtonItem></PanelSectionRow>
      </PanelSection>
    );
  }

  if (stage === "phrases") {
    return (
      <PanelSection title={`Phrase ${idx + 1} of ${phrase?.total ?? 22}`}>
        <PanelSectionRow><Field label="Say this" description={phrase ? `${phrase.text}${phrase.hint ? `\n(${phrase.hint})` : ""}` : "..."} /></PanelSectionRow>
        {recording && <PanelSectionRow><ProgressBarWithInfo label="Listening - speak now, then stop talking" nProgress={levelPct(level)} sOperationText={`${Math.round(level)} dB`} /></PanelSectionRow>}
        {recMsg && <PanelSectionRow><Field label="Try again" description={recMsg} /></PanelSectionRow>}
        {!recording && <PanelSectionRow><ButtonItem layout="below" onClick={() => void record()}>{recMsg ? "Record again" : "Record this phrase"}</ButtonItem></PanelSectionRow>}
        {!recording && <PanelSectionRow><ButtonItem layout="below" onClick={() => { if (idx + 1 >= (phrase?.total ?? 22)) setStage("running"); else setIdx(idx + 1); setRecMsg(""); }}>Skip this phrase</ButtonItem></PanelSectionRow>}
        <PanelSectionRow><ButtonItem layout="below" onClick={() => { void vtCancel(); void leave(); }}>Cancel the test</ButtonItem></PanelSectionRow>
      </PanelSection>
    );
  }

  if (stage === "running") {
    const done = run?.done ?? false;
    const overall = run && run.total ? ((run.index + (run.clips ? run.clip / run.clips : 0)) / run.total) * 100 : 0;
    return (
      <PanelSection title="Scoring the models">
        <PanelSectionRow><ProgressBarWithInfo label={run?.model ? `Testing ${run.model} (${Math.min(run.index + 1, run.total)} of ${run.total})` : "Starting..."} nProgress={Math.min(100, overall)} sOperationText={run?.model ? `phrase ${run.clip} of ${run.clips}` : ""} /></PanelSectionRow>
        <PanelSectionRow><Field label={info ? "Problem" : "Please wait"} description={info || "Each model loads and reads your recordings one by one. This can take a minute or two. Your recordings are deleted when it finishes."} /></PanelSectionRow>
        {(info || done) && <PanelSectionRow><ButtonItem layout="below" onClick={() => void leave()}>Back to the start</ButtonItem></PanelSectionRow>}
        {!done && !info && <PanelSectionRow><ButtonItem layout="below" onClick={() => { void vtCancel(); void leave(); }}>Cancel</ButtonItem></PanelSectionRow>}
      </PanelSection>
    );
  }

  // results
  const rank = results?.verdict.ranking ?? [];
  return (
    <>
      <PanelSection title="Best for your voice">
        <PanelSectionRow><Field label={best ?? "No result"} description={`${results?.verdict.reason ?? ""}\nTested ${condition === "quiet" ? "in a quiet place" : "with your usual background noise"} on ${results?.phrases ?? 0} phrases. This is a small test of one voice, so a close result is a tie.`} /></PanelSectionRow>
        {rank.map((n) => {
          const m = results!.models[n];
          const g = m.groups;
          return (
            <PanelSectionRow key={n}>
              <Field label={`${n}${n === best ? "  (suggested)" : ""}`}
                description={`${m.score ?? "-"}% wrong on average - sentences ${pct(g.sentence)}, letters and digits ${pct(g.spelled)}, numbers ${pct(g.numbers)}, sound-alikes ${pct(g.homophone)} - ${m.median_s}s per phrase${m.memory_mb ? ` - ${sizeLabel(m.memory_mb)} of memory` : ""}`} />
            </PanelSectionRow>
          );
        })}
        {Object.entries(results?.models ?? {}).filter(([, m]) => m.error).map(([n, m]) => (
          <PanelSectionRow key={n}><Field label={`${n} could not be tested`} description={m.error ?? ""} /></PanelSectionRow>
        ))}
      </PanelSection>
      <PanelSection>
        {best && <PanelSectionRow><ButtonItem layout="below" disabled={props.active === best} onClick={async () => { const r = await useModel(best); setInfo(r.ok ? `Now using ${best}.` : r.error ?? "Could not switch."); await props.refresh(); }}>{props.active === best ? `${best} is already your model` : `Use ${best}`}</ButtonItem></PanelSectionRow>}
        {others.length > 0 && !confirmDelete && <PanelSectionRow><ButtonItem layout="below" onClick={() => setConfirmDelete(true)} description="Frees the disk space. You can download them again later.">Delete the other {others.length} tested model{others.length === 1 ? "" : "s"}</ButtonItem></PanelSectionRow>}
        {others.length > 0 && confirmDelete && <PanelSectionRow><ButtonItem layout="below" onClick={async () => { for (const n of others) await dropModel(n); setConfirmDelete(false); setInfo("Deleted."); await props.refresh(); }} description={others.join(", ")}>Press again to delete them</ButtonItem></PanelSectionRow>}
        {condition !== "quiet" && <PanelSectionRow><ButtonItem layout="below" onClick={() => { setQuiet(true); setMic(null); void vtFinish(); setStage("mic"); }} description="If you can get somewhere quiet, run it again and compare.">Test again in a quiet place</ButtonItem></PanelSectionRow>}
        {info && <PanelSectionRow><Field label="Done" description={info} /></PanelSectionRow>}
        <PanelSectionRow><ButtonItem layout="below" onClick={() => void leave()}>Finish</ButtonItem></PanelSectionRow>
      </PanelSection>
    </>
  );
}
