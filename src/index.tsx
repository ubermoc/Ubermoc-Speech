import { callable } from "@decky/api";
import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ProgressBarWithInfo,
  definePlugin,
} from "@decky/ui";
import { Component, ReactNode, useEffect, useState } from "react";
import { FaMicrophone } from "react-icons/fa";

// ---------- types ----------
interface Status { model: boolean; binary: boolean; server: boolean; recording: boolean }
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
const downloadModel = callable<[], { ok: boolean; error?: string }>("download_model");
const getProgress = callable<[], number | null>("get_download_progress");
const startRecording = callable<[], { ok: boolean; error?: string }>("start_recording");
const stopAndTranscribe = callable<[], { ok: boolean; text?: string; error?: string }>(
  "stop_and_transcribe"
);
const checkUpdate = callable<[], UpdateInfo>("check_update");
const installUpdate = callable<[], { ok: boolean; version?: string; error?: string }>("install_update");
const logBackend = callable<[string], boolean>("log");

// ---------- panel ----------
type Phase = "idle" | "recording" | "transcribing";

function Panel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState("");
  const [upd, setUpd] = useState<UpdateInfo | null>(null);
  const [updMsg, setUpdMsg] = useState("");
  const [updBusy, setUpdBusy] = useState(false);

  const refresh = async () => setStatus(await getStatus());

  useEffect(() => {
    void refresh();
    void checkUpdate().then(setUpd);
    const t = setInterval(async () => {
      setProgress(await getProgress());
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const download = async () => {
    setProgress(0);
    const r = await downloadModel();
    setProgress(null);
    if (!r.ok) setResult(`Download failed: ${r.error ?? "unknown error"}`);
    await refresh();
  };

  const toggleRecording = async () => {
    if (phase === "idle") {
      const r = await startRecording();
      if (r.ok) {
        setPhase("recording");
        setResult("");
      } else setResult(r.error ?? "Could not start recording");
      return;
    }
    if (phase === "recording") {
      setPhase("transcribing");
      const r = await stopAndTranscribe();
      setPhase("idle");
      setResult(r.ok ? r.text || "(nothing heard)" : r.error ?? "Transcription failed");
      await refresh();
    }
  };

  return (
    <>
      <PanelSection title="Transcribe">
        {status && !status.binary && (
          <PanelSectionRow>
            <Field label="Problem">whisper-server is missing - reinstall the plugin</Field>
          </PanelSectionRow>
        )}
        {status && !status.model && progress === null && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={download}
              description="One-time download, kept permanently">
              Download speech model (142 MB)
            </ButtonItem>
          </PanelSectionRow>
        )}
        {progress !== null && (
          <PanelSectionRow>
            <ProgressBarWithInfo
              label="Downloading speech model"
              nProgress={progress * 100}
              sOperationText={`${Math.round(progress * 100)}%`}
            />
          </PanelSectionRow>
        )}
        {status?.model && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={toggleRecording} disabled={phase === "transcribing"}
              description={phase === "idle" ? "First use takes a few seconds to load the model" : undefined}>
              {phase === "idle" && "Start recording"}
              {phase === "recording" && "Recording... tap to stop"}
              {phase === "transcribing" && "Transcribing..."}
            </ButtonItem>
          </PanelSectionRow>
        )}
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
