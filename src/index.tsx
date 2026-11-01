import { callable } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ProgressBarWithInfo,
  definePlugin,
} from "@decky/ui";
import { Component, ReactNode, useEffect, useRef, useState } from "react";
import { FaMicrophone } from "react-icons/fa";

// ---------- types ----------
interface Status { binary: boolean; server: boolean; recording: boolean }
interface ModelInfo { name: string; size_mb: number; description: string; installed: boolean }
interface ModelList { selected: string; models: ModelInfo[] }
interface DlProgress { done: number; total: number }
interface BuildInfo { version: string; build: string }
interface UpdateInfo {
  ok: boolean;
  installed: BuildInfo;
  latest?: BuildInfo;
  available?: boolean;
  error?: string;
}

// ---------- backend ----------
const getStatus = callable<[], Status>("status");
const listModels = callable<[], ModelList>("list_models");
const setModel = callable<[string], { ok: boolean; error?: string }>("set_model");
const downloadModel = callable<[string], { ok: boolean; error?: string }>("download_model");
const getProgress = callable<[], Record<string, DlProgress>>("get_download_progress");
const cancelDownload = callable<[string], boolean>("cancel_download");
const startRecording = callable<[], { ok: boolean; error?: string; ready_s?: number }>("start_recording");
const stopAndTranscribe = callable<
  [],
  { ok: boolean; text?: string; error?: string; wait_s?: number; transcribe_s?: number }
>("stop_and_transcribe");
const checkUpdate = callable<[], UpdateInfo>("check_update");
const installUpdate = callable<[], { ok: boolean; version?: string; error?: string }>("install_update");
const logBackend = callable<[string], boolean>("log");

// ---------- panel ----------
type Phase = "idle" | "starting" | "recording" | "transcribing";

// Remembers the model being browsed, even if the panel is rebuilt.
let lastChoice = "";

const sizeLabel = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);
const MB = 1024 * 1024;
interface Speed { done: number; t: number; mbps: number }

const progressPct = (p: DlProgress) => (p.total ? Math.min(100, (p.done / p.total) * 100) : 0);
function progressText(p: DlProgress, mbps: number | undefined) {
  const parts = [`${Math.round(progressPct(p))}%`, `${Math.round(p.done / MB)} of ${Math.round(p.total / MB)} MB`];
  if (mbps && mbps > 0.05) {
    parts.push(`${mbps.toFixed(1)} MB/s`);
    const secs = Math.round((p.total - p.done) / MB / mbps);
    if (secs > 0) parts.push(secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s left` : `${secs}s left`);
  }
  return parts.join(" - ");
}

function Panel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [active, setActive] = useState("");   // model in use ("" = none downloaded/selected)
  const [choice, setChoice] = useState("");   // model being browsed in "Choose a model"
  const [progress, setProgress] = useState<Record<string, DlProgress>>({});
  const [speeds, setSpeeds] = useState<Record<string, number>>({});
  const [dlError, setDlError] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState("");
  const [timing, setTiming] = useState("");
  const [upd, setUpd] = useState<UpdateInfo | null>(null);
  const [updMsg, setUpdMsg] = useState("");
  const [updBusy, setUpdBusy] = useState(false);
  const wasDownloading = useRef(false);
  const speedRef = useRef<Record<string, Speed>>({});

  const refresh = async () => {
    const m = await listModels();
    setModels(m.models);
    setActive(m.selected);
    setChoice((c) => c || lastChoice || m.selected || m.models[0]?.name || "");
    setStatus(await getStatus());
  };

  useEffect(() => {
    void logBackend("panel mounted");
    void refresh();
    void checkUpdate().then(setUpd);
    const t = setInterval(async () => {
      const p = await getProgress();
      setProgress(p);
      // download speed from how much arrived since the last check
      const now = Date.now();
      const next: Record<string, Speed> = {};
      const mbps: Record<string, number> = {};
      for (const [name, pr] of Object.entries(p)) {
        const prev = speedRef.current[name];
        let speed = prev?.mbps ?? 0;
        if (prev && now > prev.t) {
          const inst = (pr.done - prev.done) / MB / ((now - prev.t) / 1000);
          speed = prev.mbps ? prev.mbps * 0.7 + inst * 0.3 : inst;
        }
        next[name] = { done: pr.done, t: now, mbps: speed };
        mbps[name] = speed;
      }
      speedRef.current = next;
      setSpeeds(mbps);
      const busy = Object.keys(p).length > 0;
      if (wasDownloading.current && !busy) void refresh(); // a download just finished
      wasDownloading.current = busy;
    }, 500);
    return () => clearInterval(t);
  }, []);

  const downloaded = models.filter((m) => m.installed);
  const chosen = models.find((m) => m.name === choice);
  const activeInfo = models.find((m) => m.name === active);
  const ready = !!activeInfo?.installed;
  const downloading = Object.entries(progress);

  const startDownload = async (m: ModelInfo) => {
    setDlError("");
    setProgress((p) => ({ ...p, [m.name]: { done: 0, total: m.size_mb * MB } }));
    wasDownloading.current = true;
    const r = await downloadModel(m.name);
    if (!r.ok) setDlError(`${m.name}: ${r.error ?? "download failed"}`);
    await refresh();
  };

  // Only downloaded models can be made current.
  const useModel = async (name: string) => {
    const r = await setModel(name);
    if (r.ok) setActive(name);
    else setDlError(r.error ?? "Could not switch model");
    await refresh();
  };

  const toggleRecording = async () => {
    if (phase === "idle") {
      setPhase("starting");
      setResult("");
      setTiming("");
      const r = await startRecording();
      if (r.ok) {
        setTiming(`Microphone ready after ${(r.ready_s ?? 0).toFixed(1)} s`);
        setPhase("recording");
      } else {
        setResult(r.error ?? "Could not start recording");
        setPhase("idle");
      }
      return;
    }
    if (phase === "recording") {
      setPhase("transcribing");
      const r = await stopAndTranscribe();
      setPhase("idle");
      if (r.ok) {
        setResult(r.text || "(nothing heard)");
        setTiming((t) => `${t} - text ready ${((r.wait_s ?? 0) + (r.transcribe_s ?? 0)).toFixed(1)} s after you stopped`);
      } else setResult(r.error ?? "Transcription failed");
    }
  };

  return (
    <>
      <PanelSection title="Current model">
        {downloaded.length > 0 ? (
          <PanelSectionRow>
            <DropdownItem
              label="Model"
              description={activeInfo?.description ?? "Pick one of your downloaded models"}
              rgOptions={downloaded.map((m) => ({ data: m.name, label: m.name }))}
              selectedOption={ready ? active : undefined}
              strDefaultLabel="Select a model"
              onChange={(o) => useModel(o.data as string)}
            />
          </PanelSectionRow>
        ) : (
          <PanelSectionRow>
            <Field label="No model yet" description="Download one under Choose a model, then select it here" />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Choose a model">
        <PanelSectionRow>
          <DropdownItem
            label="Model"
            description={
              chosen
                ? `${chosen.description} (${chosen.installed ? "downloaded" : sizeLabel(chosen.size_mb)})`
                : "Pick a model"
            }
            rgOptions={models.map((m) => ({
              data: m.name,
              label: m.installed ? `${m.name}  -  downloaded` : `${m.name}  -  ${sizeLabel(m.size_mb)}`,
            }))}
            selectedOption={choice}
            onChange={(o) => {
              const name = o.data as string;
              lastChoice = name;
              setChoice(name);
              setDlError("");
              void logBackend(`choose model: ${name}`);
            }}
          />
        </PanelSectionRow>
        {chosen && !chosen.installed && !progress[chosen.name] && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => startDownload(chosen)}>
              {dlError ? "Retry download" : "Download"} {chosen.name} ({sizeLabel(chosen.size_mb)})
            </ButtonItem>
          </PanelSectionRow>
        )}
        {downloading.map(([name, p]) => (
          <div key={name}>
            <PanelSectionRow>
              <ProgressBarWithInfo
                label={`Downloading ${name}`}
                nProgress={progressPct(p)}
                sOperationText={progressText(p, speeds[name])}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => cancelDownload(name)}>
                Cancel download
              </ButtonItem>
            </PanelSectionRow>
          </div>
        ))}
        {dlError && (
          <PanelSectionRow>
            <Field label="Problem">{dlError}</Field>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Transcribe">
        {status && !status.binary && (
          <PanelSectionRow>
            <Field label="Problem">whisper-server is missing - reinstall the plugin</Field>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={toggleRecording}
            disabled={!ready || phase === "starting" || phase === "transcribing"}
            description={!ready ? "Select a downloaded model first" : timing || undefined}
          >
            {phase === "idle" && "Start recording"}
            {phase === "starting" && "Starting microphone... wait"}
            {phase === "recording" && "SPEAK NOW - tap to stop"}
            {phase === "transcribing" && "Transcribing..."}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Text">
            <div style={{ whiteSpace: "pre-wrap", textAlign: "right" }}>{result || "-"}</div>
          </Field>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Updates">
        <PanelSectionRow>
          <Field label="Installed version">v{upd?.installed.version ?? "..."}</Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Latest version">
            {upd?.latest
              ? `v${upd.latest.version}${
                  upd.available && upd.latest.version === upd.installed.version ? " (rebuilt)" : ""
                }`
              : upd?.error
                ? "unavailable"
                : "..."}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={updBusy}
            description={updMsg || (upd?.error ?? (upd?.available ? "A newer version is available" : "You are up to date"))}
            onClick={async () => {
              setUpdBusy(true);
              try {
                if (upd?.available) {
                  setUpdMsg("Downloading and installing...");
                  const r = await installUpdate();
                  setUpdMsg(
                    r.ok
                      ? "Updated. Decky is restarting - reopen this menu in a few seconds."
                      : `Update failed: ${r.error ?? "unknown error"}`
                  );
                } else {
                  setUpdMsg("");
                  setUpd(await checkUpdate());
                }
              } finally {
                setUpdBusy(false);
              }
            }}
          >
            {upd?.available ? "Install update" : "Check for updates"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

// Shows the real error on screen (and logs it) instead of Decky's generic page.
class Boundary extends Component<{ children?: ReactNode }, { err: string | null }> {
  state = { err: null as string | null };
  static getDerivedStateFromError(e: unknown) {
    const err = e as Error;
    return { err: String(err?.stack ?? err).slice(0, 700) };
  }
  componentDidCatch(e: unknown) {
    const err = e as Error;
    void logBackend(`render error: ${err?.stack ?? err}`);
  }
  render() {
    if (this.state.err === null) return this.props.children;
    return (
      <PanelSection title="Ubermoc Speech hit an error">
        <PanelSectionRow>
          <div style={{ whiteSpace: "pre-wrap", fontSize: 11 }}>{this.state.err}</div>
        </PanelSectionRow>
      </PanelSection>
    );
  }
}

export default definePlugin(() => ({
  name: "Ubermoc Speech",
  titleView: <div>Ubermoc Speech</div>,
  content: (
    <Boundary>
      <Panel />
    </Boundary>
  ),
  icon: <FaMicrophone />,
}));
