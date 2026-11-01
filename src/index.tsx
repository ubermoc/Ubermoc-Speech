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
import { Component, ReactNode, useEffect, useState } from "react";
import { FaMicrophone } from "react-icons/fa";

// ---------- types ----------
interface Status { binary: boolean; server: boolean; recording: boolean }
interface ModelInfo { name: string; size_mb: number; description: string; installed: boolean }
interface ModelList { selected: string; models: ModelInfo[] }
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
const getProgress = callable<[], Record<string, number>>("get_download_progress");
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

const sizeLabel = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);

function Panel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [active, setActive] = useState("");   // model the server uses
  const [choice, setChoice] = useState("");   // model shown in the dropdown
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState("");
  const [timing, setTiming] = useState("");
  const [upd, setUpd] = useState<UpdateInfo | null>(null);
  const [updMsg, setUpdMsg] = useState("");
  const [updBusy, setUpdBusy] = useState(false);

  const refresh = async () => {
    const m = await listModels();
    setModels(m.models);
    setActive(m.selected);
    setChoice((c) => c || m.selected);
    setStatus(await getStatus());
  };

  useEffect(() => {
    void refresh();
    void checkUpdate().then(setUpd);
    const t = setInterval(async () => {
      setProgress(await getProgress());
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const chosen = models.find((m) => m.name === choice);
  const activeInfo = models.find((m) => m.name === active);
  const ready = !!activeInfo?.installed;

  const pickModel = async (name: string) => {
    setChoice(name);
    const m = models.find((x) => x.name === name);
    if (m?.installed) {
      const r = await setModel(name);
      if (r.ok) setActive(name);
      else setResult(r.error ?? "Could not switch model");
    }
  };

  const download = async (m: ModelInfo) => {
    setProgress({ ...progress, [m.name]: 0 });
    const r = await downloadModel(m.name);
    if (!r.ok) setResult(`Download failed: ${r.error ?? "unknown error"}`);
    await refresh();
    if (r.ok) {
      const s = await setModel(m.name); // use the model that was just downloaded
      if (s.ok) setActive(m.name);
      await refresh();
    }
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
      <PanelSection title="Model">
        <PanelSectionRow>
          <DropdownItem
            label="Model"
            description={
              ready
                ? `In use: ${active}`
                : `${active || "Selected model"} is not downloaded yet`
            }
            rgOptions={models.map((m) => ({
              data: m.name,
              label: m.installed ? `${m.name}  -  downloaded` : `${m.name}  -  ${sizeLabel(m.size_mb)}`,
            }))}
            selectedOption={choice}
            onChange={(o) => pickModel(o.data as string)}
          />
        </PanelSectionRow>
        {chosen && (
          <PanelSectionRow>
            <Field label={chosen.name}>{chosen.description}</Field>
          </PanelSectionRow>
        )}
        {chosen && !chosen.installed && progress[chosen.name] === undefined && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => download(chosen)}
              description="One-time download, kept permanently">
              Download {chosen.name} ({sizeLabel(chosen.size_mb)})
            </ButtonItem>
          </PanelSectionRow>
        )}
        {chosen && progress[chosen.name] !== undefined && (
          <PanelSectionRow>
            <ProgressBarWithInfo
              label={`Downloading ${chosen.name}`}
              nProgress={progress[chosen.name] * 100}
              sOperationText={`${Math.round(progress[chosen.name] * 100)}%`}
            />
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
            description={!ready ? "Download the selected model first" : timing || undefined}
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
