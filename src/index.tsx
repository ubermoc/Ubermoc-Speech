import { addEventListener, callable, removeEventListener } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  Field,
  Focusable,
  PanelSection,
  PanelSectionRow,
  ProgressBarWithInfo,
  Router,
  definePlugin,
} from "@decky/ui";
import { Component, ReactNode, useEffect, useRef, useState } from "react";
import { FaMicrophone } from "react-icons/fa";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const SteamClient: any;

// ---------- types ----------
interface Status { binary: boolean; server: boolean; recording: boolean }
type Phase = "idle" | "starting" | "recording" | "transcribing" | "typing";
interface ModelInfo { name: string; size_mb: number; description: string; installed: boolean }
interface ModelList { selected: string; models: ModelInfo[] }
interface DlProgress { done: number; total: number }
interface TypedInfo {
  status: "typed" | "panel" | "empty" | "error";
  method?: string; typed?: number; skipped?: number; note?: string; error?: string;
  before?: string | null; after?: string | null; app?: string | null;
}
interface OverrideInfo { appid: string; name: string; kind: "steam" | "shortcut"; before: KeyAction; after: KeyAction; symbols: "words" | "symbols" }
interface OverridesInfo { overrides: OverrideInfo[]; running: { appid: string; name: string; has_override: boolean } }
interface LibApp { appid: string; name: string }
interface KeyAction { mode: "none" | "enter" | "custom"; keys: string[]; label: string; warnings: string[] }
interface LastResult {
  ok: boolean; text?: string; error?: string; wait_s?: number; transcribe_s?: number; by?: string;
  typed?: TypedInfo;
}
interface TypingInfo {
  symbols: "words" | "symbols";
  method: "keyboard" | "steam";
  speed: "fast" | "normal" | "slow";
  keyboard: { available: boolean; reason: string };
  before: KeyAction;
  after: KeyAction;
  test: (TypedInfo & { at: number }) | null;
}
interface DictState { phase: Phase; last: LastResult | null; activator_held: boolean }
interface Watching {
  running: boolean; keyboards: number; mice: number; pads: number;
  controllers: string[]; unreadable: number; unrecognized: string[];
}
interface ActivatorInfo { tokens: string[]; label: string; warnings: string[]; watching: Watching }
interface CaptureStatus { state: "idle" | "active" | "done" | "timeout" | "cancelled"; tokens: string[]; label: string; warnings?: string[] }
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
const deleteModel = callable<[string], { ok: boolean; error?: string }>("delete_model");
const startRecording = callable<[], { ok: boolean; error?: string; ready_s?: number }>("start_recording");
const stopAndTranscribe = callable<
  [],
  { ok: boolean; text?: string; error?: string; wait_s?: number; transcribe_s?: number }
>("stop_and_transcribe");
const checkUpdate = callable<[], UpdateInfo>("check_update");
const installUpdate = callable<[], { ok: boolean; version?: string; error?: string }>("install_update");
const getState = callable<[], DictState>("get_state");
const getActivator = callable<[], ActivatorInfo>("get_activator");
const startCapture = callable<[], { ok: boolean; error?: string }>("start_capture");
const captureStatus = callable<[], CaptureStatus>("capture_status");
const cancelCapture = callable<[], boolean>("cancel_capture");
const clearActivator = callable<[], { ok: boolean }>("clear_activator");
const setUiPad = callable<[string[]], boolean>("set_ui_pad");
const getTyping = callable<[], TypingInfo>("get_typing");
const setTyping = callable<[string, string, string], TypingInfo>("set_typing");
const typeTest = callable<[number], { ok: boolean }>("type_test");
const setKeyAction = callable<[string, string, string[], string], TypingInfo & OverridesInfo>("set_key_action");
const startKeyCapture = callable<[string, string], { ok: boolean; error?: string }>("start_key_capture");
const getOverrides = callable<[], OverridesInfo>("get_overrides");
const addOverride = callable<[string, string, string], OverridesInfo>("add_override");
const setOverrideSymbols = callable<[string, string], OverridesInfo>("set_override_symbols");
const removeOverride = callable<[string], OverridesInfo>("remove_override");
const setRunningApp = callable<[string, string], boolean>("set_running_app");
const typeAck = callable<[string, boolean], boolean>("type_ack");
const logBackend = callable<[string], boolean>("log");

// ---------- panel ----------

// ---------- Steam interface controller feed ----------
// Forwards the buttons Steam reports (works for any controller Steam supports, but
// only while the Steam interface is active, not in a game) to the backend, which
// reads the controllers' raw data for in-game use. Steam's callback changed shape
// between builds: older builds pass an array of {nA: button, bS: pressed}, newer
// ones pass (controllerIndex, button, pressed). Both are handled, and the
// subscription is renewed after sleep because the feed stops delivering then.
const STEAM_ID_NAMES: Record<number, string> = {
  0: "A", 1: "B", 2: "X", 3: "Y",
  4: "D-pad Up", 5: "D-pad Right", 6: "D-pad Down", 7: "D-pad Left",
  8: "Menu", 9: "View", 25: "L3", 41: "R3",
  28: "L2", 29: "R2", 30: "L1", 31: "R1",
  32: "L5", 33: "R5", 44: "L4", 45: "R4",
  34: "Guide", 35: "View", 36: "Menu",
  37: "Left pad click", 39: "Right pad click",
};
const uiHeld = new Set<string>();
let uiHeartbeat: ReturnType<typeof setInterval> | null = null;

function pushUi() {
  void setUiPad([...uiHeld]);
  if (uiHeld.size && !uiHeartbeat) uiHeartbeat = setInterval(() => void setUiPad([...uiHeld]), 1000);
  if (!uiHeld.size && uiHeartbeat) {
    clearInterval(uiHeartbeat);
    uiHeartbeat = null;
  }
}

function uiButton(id: number, down: boolean) {
  const name = STEAM_ID_NAMES[id];
  if (!name) return;
  const token = `pad:${name}`;
  if (down) uiHeld.add(token);
  else uiHeld.delete(token);
  pushUi();
}

// Remembers the model being browsed, even if the panel is rebuilt.
let lastChoice = "";
// Which groups are expanded; kept at module level so closing and reopening the menu keeps them.
let openGroup: string | null = null;
// The app being edited in Applications; module level because Decky can re-create the panel.
let editedApp = "";

// ---------- installed games and non-Steam apps ----------
// Steam's own definition (its interface source): an installed Steam game is a Game whose
// local_per_client_data.installed is true; that is what collectionStore.localGamesCollection
// holds. Non-Steam apps are the library entries of shortcut type (1073741824).
const APP_GAME = 1;
const APP_SHORTCUT = 1073741824;

function installedApps(): { steam: LibApp[]; other: LibApp[] } {
  const steam: LibApp[] = [];
  const other: LibApp[] = [];
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = window as any;
    const local = w.collectionStore?.localGamesCollection?.allApps;
    const all = w.appStore?.allApps;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const installed: any[] = Array.isArray(local)
      ? local
      : // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (Array.isArray(all) ? all : []).filter((a: any) => a?.app_type === APP_GAME && a?.local_per_client_data?.installed === true);
    for (const a of installed) {
      if (!a?.appid || !a?.display_name || a.app_type === APP_SHORTCUT) continue;
      steam.push({ appid: String(a.appid), name: String(a.display_name) });
    }
    for (const a of Array.isArray(all) ? all : []) {
      if (a?.app_type === APP_SHORTCUT && a?.appid && a?.display_name)
        other.push({ appid: String(a.appid), name: String(a.display_name) });
    }
  } catch (e) {
    void logBackend(`installedApps failed: ${e}`);
  }
  const byName = (x: LibApp, y: LibApp) => x.name.localeCompare(y.name);
  return { steam: steam.sort(byName), other: other.sort(byName) };
}

// While an input is being recorded, the menu must ignore the controller: a press would
// otherwise click Cancel, move the focus or close the menu before it was recorded.
// This box takes focus and consumes every controller press the way Steam's own
// interface code does it (it calls stopPropagation() and preventDefault() in
// onButtonDown, and returns true from onGamepadDirection). It has no button inside, so
// nothing can be "pressed" by a controller. Touch or mouse (pointer events, which a
// controller never produces) cancels; otherwise it stops by itself after 15 seconds.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const swallow = (e: any) => {
  e?.stopPropagation?.();
  e?.preventDefault?.();
};
const consumeDirection = (() => true) as unknown as () => void;

function ListeningBox(props: { value: string; secondsLeft: number; onTouchCancel: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus?.();
  }, []);
  return (
    <Focusable
      ref={ref}
      preferredFocus
      noFocusRing
      onButtonDown={swallow}
      onButtonUp={swallow}
      onOKButton={swallow}
      onCancelButton={swallow}
      onSecondaryButton={swallow}
      onOptionsButton={swallow}
      onGamepadDirection={consumeDirection}
      onCancel={swallow}
      onPointerUp={(e) => {
        if (e.pointerType === "touch" || e.pointerType === "mouse") props.onTouchCancel();
      }}
      style={{ width: "100%", padding: "8px 0" }}
    >
      <div style={{ fontWeight: "bold" }}>Listening... {props.value || "waiting for input"}</div>
      <div style={{ opacity: 0.8, fontSize: 12, marginTop: 4 }}>
        Press and hold your keys or buttons together, then let go. Works for keyboard, mouse and
        controller. The menu ignores your controller while this is listening. To use the button you
        tapped to start, press it again or keep holding it.
      </div>
      <div style={{ opacity: 0.8, fontSize: 12, marginTop: 4 }}>
        Stops by itself in {props.secondsLeft} s. Touch here to cancel.
      </div>
    </Focusable>
  );
}

function GroupHeader(props: { label: string; summary: string; open: boolean; onToggle: () => void }) {
  return (
    <PanelSection>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={props.onToggle} description={props.open ? undefined : props.summary}>
          {props.open ? "v " : "> "}
          {props.label}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

const typedText = (t?: TypedInfo | null): string => {
  if (!t) return "";
  switch (t.status) {
    case "typed":
      return (
        `Typed ${t.typed ?? 0} characters` +
        (t.method === "steam" ? " (Steam keyboard)" : "") +
        (t.skipped ? `, ${t.skipped} could not be typed` : "") +
        (t.before ? `; ${t.before} before` : "") +
        (t.after ? `; ${t.after} after` : "") +
        (t.app ? ` (overrides for ${t.app})` : "") +
        (t.note ? ` - ${t.note}` : "")
      );
    case "panel": return "Not typed: this menu was open, so the text is only shown here";
    case "empty": return "Nothing to type";
    default: return `Could not type: ${t.error ?? "unknown error"}`;
  }
};

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
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [dict, setDict] = useState<DictState>({ phase: "idle", last: null, activator_held: false });
  const [startError, setStartError] = useState("");
  const [act, setAct] = useState<ActivatorInfo | null>(null);
  // "activator", or "<before|after>@<appid>" ("" appid = Global options)
  const [capTarget, setCapTarget] = useState<string | null>(null);
  const [ovr, setOvr] = useState<OverridesInfo | null>(null);
  const [selApp, setSelAppState] = useState<string>(editedApp);
  const setSelApp = (id: string) => { editedApp = id; setSelAppState(id); };
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [libs, setLibs] = useState<{ steam: LibApp[]; other: LibApp[] }>({ steam: [], other: [] });
  const capturing = capTarget === "activator";
  const [keyMsg, setKeyMsg] = useState<Record<string, string>>({});
  const [capStart, setCapStart] = useState(0);
  const [secondsLeft, setSecondsLeft] = useState(15);
  const [cap, setCap] = useState<CaptureStatus | null>(null);
  const [capMsg, setCapMsg] = useState("");
  const [typing, setTypingInfo] = useState<TypingInfo | null>(null);
  const [typeMsg, setTypeMsg] = useState("");
  const [open, setOpen] = useState<string | null>(openGroup);
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
    void getActivator().then(setAct);
    void getTyping().then(setTypingInfo);
    const st = setInterval(async () => setDict(await getState()), 400);
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
    return () => {
      clearInterval(t);
      clearInterval(st);
    };
  }, []);

  // While recording an activator or a key combination, follow what the backend hears.
  useEffect(() => {
    if (!capTarget) return;
    const target = capTarget;
    const t = setInterval(async () => {
      setSecondsLeft(Math.max(0, 15 - Math.floor((Date.now() - capStart) / 1000)));
      const c = await captureStatus();
      setCap(c);
      if (target === "activator") setAct(await getActivator());
      if (c.state !== "active") {
        setCapTarget(null);
        const msg =
          c.state === "done" ? `Saved: ${c.label}`
          : c.state === "timeout" ? "Nothing was pressed - try again"
          : "Cancelled";
        if (target === "activator") {
          setCapMsg(msg);
          setAct(await getActivator());
        } else {
          setKeyMsg((m) => ({ ...m, [target]: msg }));
          if (target.endsWith("@")) setTypingInfo(await getTyping());
          else setOvr(await getOverrides());
        }
      }
    }, 300);
    return () => clearInterval(t);
  }, [capTarget, capStart]);

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

  // Two taps: the first asks, the second deletes.
  const removeModel = async (m: ModelInfo) => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    setConfirmDelete(false);
    const r = await deleteModel(m.name);
    if (!r.ok) setDlError(`${m.name}: ${r.error ?? "could not delete"}`);
    else setDlError("");
    await refresh();
  };

  // Only downloaded models can be made current.
  const useModel = async (name: string) => {
    const r = await setModel(name);
    if (r.ok) setActive(name);
    else setDlError(r.error ?? "Could not switch model");
    await refresh();
  };

  useEffect(() => {
    void getOverrides().then(setOvr).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (open !== "apps") return;
    setLibs(installedApps());
    void getOverrides().then(setOvr).catch(() => undefined);
    const t = setInterval(() => { void getOverrides().then(setOvr).catch(() => undefined); }, 3000);
    return () => clearInterval(t);
  }, [open]);

  // Accordion: opening a group closes the one that was open.
  const toggleGroup = (key: string) => {
    openGroup = open === key ? null : key;
    setOpen(openGroup);
  };

  const phase = dict.phase;
  const phaseText =
    phase === "idle" ? "Idle"
    : phase === "starting" ? "Starting microphone..."
    : phase === "recording" ? "Listening..."
    : phase === "transcribing" ? "Transcribing..."
    : "Typing...";
  const last = dict.last;
  const resultText = last ? (last.ok ? last.text || "(nothing heard)" : last.error ?? "Transcription failed") : "";
  const timing =
    last?.ok && last.transcribe_s !== undefined
      ? `${last.by === "activator" ? "Heard via the activator" : "Heard"} - text ready ${(
          (last.wait_s ?? 0) + (last.transcribe_s ?? 0)
        ).toFixed(1)} s after you stopped`
      : "";

  const toggleRecording = async () => {
    setStartError("");
    if (phase === "idle") {
      const r = await startRecording();
      if (!r.ok) setStartError(r.error ?? "Could not start recording");
    } else if (phase === "recording") {
      await stopAndTranscribe();
    }
    setDict(await getState());
  };

  const beginCapture = async () => {
    setCapMsg("");
    setCap(null);
    const r = await startCapture();
    if (r.ok) {
      setCapStart(Date.now());
      setSecondsLeft(15);
      setCapTarget("activator");
    }
    else setCapMsg(r.error ?? "Could not start listening");
  };

  const applyKeyAction = async (which: "before" | "after", mode: string, keys: string[], appid: string) => {
    setKeyMsg((m) => ({ ...m, [`${which}@${appid}`]: "" }));
    const r = await setKeyAction(which, mode, keys, appid);
    if (appid) setOvr(r);
    else setTypingInfo(r);
  };

  const beginKeyCapture = async (which: "before" | "after", appid: string) => {
    const id = `${which}@${appid}`;
    setKeyMsg((m) => ({ ...m, [id]: "" }));
    setCap(null);
    const r = await startKeyCapture(which, appid);
    if (r.ok) {
      setCapStart(Date.now());
      setSecondsLeft(15);
      setCapTarget(id);
    }
    else setKeyMsg((m) => ({ ...m, [id]: r.error ?? "Could not start listening" }));
  };

  // What is pressed around the typed text: None, Enter, or anything you record
  // (keyboard, mouse or controller; a single input or a combination).
  const keyActionRows = (which: "before" | "after", label: string, hint: string, appid = "") => {
    const found = appid ? ovr?.overrides.find((o) => o.appid === appid)?.[which] : typing?.[which];
    const a: KeyAction = found ?? { mode: "enter", keys: [], label: "Enter", warnings: [] };
    const id = `${which}@${appid}`;
    const listening = capTarget === id;
    return (
      <>
        <PanelSectionRow>
          <DropdownItem
            label={label}
            description={hint}
            rgOptions={[
              { data: "none", label: "None" },
              { data: "enter", label: "Enter" },
              { data: "custom", label: "Any key or combination" },
            ]}
            selectedOption={a.mode}
            onChange={(o) => applyKeyAction(which, o.data as string, o.data === "custom" ? a.keys : [], appid)}
          />
        </PanelSectionRow>
        {a.mode === "custom" && (
          <>
            <PanelSectionRow>
              <Field label="Input">{listening ? (cap?.label || "waiting for input...") : a.label}</Field>
            </PanelSectionRow>
            {listening ? (
              <PanelSectionRow>
                <ListeningBox value={cap?.label ?? ""} secondsLeft={secondsLeft} onTouchCancel={() => cancelCapture()} />
              </PanelSectionRow>
            ) : (
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={() => beginKeyCapture(which, appid)} description={keyMsg[id] || undefined}>
                  Record key or combination
                </ButtonItem>
              </PanelSectionRow>
            )}
            {a.warnings.map((w) => (
              <PanelSectionRow key={w}>
                <Field label="Note">{w}</Field>
              </PanelSectionRow>
            ))}
          </>
        )}
      </>
    );
  };

  const applyTyping = async (patch: Partial<Pick<TypingInfo, "symbols" | "method" | "speed">>) => {
    const cur = typing ?? { symbols: "words", method: "keyboard", speed: "normal" };
    setTypingInfo(await setTyping(patch.symbols ?? cur.symbols, patch.method ?? cur.method, patch.speed ?? cur.speed));
  };

  const runTypingTest = async () => {
    await typeTest(5);
    for (let i = 5; i > 0; i--) {
      setTypeMsg(`Close this menu and click into a text box. Typing in ${i}...`);
      await new Promise((r) => setTimeout(r, 1000));
    }
    await new Promise((r) => setTimeout(r, 1200));
    const t = await getTyping();
    setTypingInfo(t);
    setTypeMsg(t.test ? typedText(t.test) : "No answer from the typing test");
  };

  const watchText = (w?: Watching) => {
    if (!w || !w.running) return "Not reading any devices (no activator set)";
    const parts: string[] = [...(w.controllers ?? [])];
    if (w.keyboards) parts.push(w.keyboards > 1 ? `${w.keyboards} keyboards` : "1 keyboard");
    if (w.mice) parts.push(w.mice > 1 ? `${w.mice} mice` : "1 mouse");
    if (w.pads) parts.push(w.pads > 1 ? `${w.pads} other controllers` : "1 other controller");
    const text = parts.length ? parts.join(", ") : "no devices found";
    return w.unreadable ? `${text} (${w.unreadable} could not be opened)` : text;
  };

  const curApp = ovr?.overrides.find((o) => o.appid === selApp) ?? ovr?.overrides[0];
  const appsSummary = ovr?.running?.name
    ? `Running: ${ovr.running.name}`
    : ovr?.overrides.length ? `${ovr.overrides.length} override${ovr.overrides.length > 1 ? "s" : ""}` : "None yet";

  const addApp = async (appid: string, name: string, kind: string) => {
    const r = await addOverride(appid, name, kind);
    setOvr(r);
    setSelApp(appid);
    setConfirmRemove(false);
  };

  return (
    <>
      <GroupHeader label="Global options" summary={act?.label ? act.label : "Activator not set"} open={open === "global"} onToggle={() => toggleGroup("global")} />
      {open === "global" && (
        <>
      <PanelSection title="Activator">
        <PanelSectionRow>
          <Field
            label="Hold to talk"
            description="Hold it to record, release to transcribe. Keyboard keys, mouse buttons and controller buttons all work, alone or as a combination."
          >
            {act?.label || "Not set"}
          </Field>
        </PanelSectionRow>
        {capturing ? (
          <PanelSectionRow>
            <ListeningBox value={cap?.label ?? ""} secondsLeft={secondsLeft} onTouchCancel={() => cancelCapture()} />
          </PanelSectionRow>
        ) : (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={beginCapture} description={capMsg || undefined}>
              {act?.label ? "Change activator" : "Set activator"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {!capturing && act?.label && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={async () => {
                await clearActivator();
                setCapMsg("");
                setAct(await getActivator());
              }}
            >
              Clear activator
            </ButtonItem>
          </PanelSectionRow>
        )}
        {(act?.watching?.unrecognized ?? []).length > 0 && (
          <PanelSectionRow>
            <Field label="Note">
              Found Valve device {(act?.watching?.unrecognized ?? []).join(", ")} that this plugin cannot read yet.
              Buttons still work while the Steam menu is open.
            </Field>
          </PanelSectionRow>
        )}
        {(act?.warnings ?? []).map((w) => (
          <PanelSectionRow key={w}>
            <Field label="Note">{w}</Field>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <Field
            label="Reading"
            description="Devices are only read while an activator is set or being recorded. Nothing is logged or stored except the activator you choose."
          >
            {watchText(act?.watching)}
          </Field>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Around the typed text">
        {keyActionRows("before", "Press before typing", "Pressed first, then the text is typed. Enter opens a chat box in most games.")}
        {keyActionRows("after", "Press after typing", "Pressed once the text is typed. Enter sends it.")}
      </PanelSection>

      <PanelSection title="Typing">
        <PanelSectionRow>
          <DropdownItem
            label="Spoken symbols"
            description={
              (typing?.symbols ?? "words") === "symbols"
                ? 'Saying "backslash" types \\ . Also: forward slash, underscore, at sign, hyphen, open/close parenthesis, bracket and brace, semicolon, ampersand, asterisk, plus sign, equals sign, dollar sign, percent sign, hashtag, tilde, caret, vertical bar, backtick.'
                : 'Saying "backslash" types the word backslash.'
            }
            rgOptions={[
              { data: "words", label: "Type the word (backslash)" },
              { data: "symbols", label: "Type the symbol (\\)" },
            ]}
            selectedOption={typing?.symbols ?? "words"}
            onChange={(o) => applyTyping({ symbols: o.data as TypingInfo["symbols"] })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Typing method"
            description={
              (typing?.method ?? "keyboard") === "steam"
                ? "Uses Steam's own keyboard. Gaming mode only; falls back to the virtual keyboard if Steam does not answer."
                : "A virtual keyboard created by the plugin. Works everywhere, including Steam's search box."
            }
            rgOptions={[
              { data: "keyboard", label: "Virtual keyboard (works everywhere)" },
              { data: "steam", label: "Steam keyboard (gaming mode only)" },
            ]}
            selectedOption={typing?.method ?? "keyboard"}
            onChange={(o) => applyTyping({ method: o.data as TypingInfo["method"] })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Typing speed"
            description="Use Slow if a game drops letters."
            rgOptions={[
              { data: "fast", label: "Fast" },
              { data: "normal", label: "Normal" },
              { data: "slow", label: "Slow" },
            ]}
            selectedOption={typing?.speed ?? "normal"}
            onChange={(o) => applyTyping({ speed: o.data as TypingInfo["speed"] })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="Virtual keyboard"
            description="Types by key position, so it assumes a US keyboard layout and plain English characters."
          >
            {typing ? (typing.keyboard.available ? "Ready" : `Unavailable: ${typing.keyboard.reason}`) : "..."}
          </Field>
        </PanelSectionRow>
      </PanelSection>

        </>
      )}

      <GroupHeader label="Applications" summary={appsSummary} open={open === "apps"} onToggle={() => toggleGroup("apps")} />
      {open === "apps" && (
        <>
      <PanelSection title="Per-application overrides">
        <PanelSectionRow>
          <Field
            label="Running now"
            description="Press before / after typing can be different for a game or app. Anything without an override uses Global options. Works in gaming mode, where Steam knows what is running."
          >
            {ovr?.running?.name ? `${ovr.running.name}${ovr.running.has_override ? " (override)" : " (Global options)"}` : "Nothing"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Steam applications"
            description="Installed games. Choosing one adds an override for it."
            strDefaultLabel={libs.steam.length ? "Choose a game..." : "No installed games found"}
            rgOptions={libs.steam.map((a) => ({ data: a.appid, label: a.name }))}
            selectedOption={null}
            onChange={(o) => addApp(o.data as string, o.label as string, "steam")}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Non-Steam applications"
            description="Apps added to your Steam library as non-Steam shortcuts."
            strDefaultLabel={libs.other.length ? "Choose an app..." : "No non-Steam apps found"}
            rgOptions={libs.other.map((a) => ({ data: a.appid, label: a.name }))}
            selectedOption={null}
            onChange={(o) => addApp(o.data as string, o.label as string, "shortcut")}
          />
        </PanelSectionRow>
        {(ovr?.overrides.length ?? 0) > 0 && (
          <PanelSectionRow>
            <DropdownItem
              label="Edit override for"
              rgOptions={(ovr?.overrides ?? []).map((o) => ({
                data: o.appid,
                label: `${o.name} - ${o.before.label} / ${o.after.label}`,
              }))}
              selectedOption={curApp?.appid ?? null}
              onChange={(o) => { setSelApp(o.data as string); setConfirmRemove(false); }}
            />
          </PanelSectionRow>
        )}
      </PanelSection>

      {curApp && (
        <PanelSection title={curApp.name}>
          {keyActionRows("before", "Press before typing", "Pressed first, then the text is typed.", curApp.appid)}
          {keyActionRows("after", "Press after typing", "Pressed once the text is typed.", curApp.appid)}
          <PanelSectionRow>
            <DropdownItem
              label="Spoken symbols"
              description={curApp.symbols === "symbols" ? 'Saying "backslash" types \\ in this app.' : 'Saying "backslash" types the word in this app.'}
              rgOptions={[
                { data: "words", label: "Type the word (backslash)" },
                { data: "symbols", label: "Type the symbol (\\)" },
              ]}
              selectedOption={curApp.symbols}
              onChange={async (o) => setOvr(await setOverrideSymbols(curApp.appid, o.data as string))}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={async () => {
                if (!confirmRemove) { setConfirmRemove(true); return; }
                setConfirmRemove(false);
                const r = await removeOverride(curApp.appid);
                setOvr(r);
                setSelApp("");
              }}
              description={confirmRemove ? "Press again to confirm" : "Goes back to Global options for this app"}
            >
              {confirmRemove ? "Confirm: remove override" : "Remove override"}
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
        </>
      )}

      <GroupHeader label="Models" summary={active || "No model yet"} open={open === "models"} onToggle={() => toggleGroup("models")} />
      {open === "models" && (
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
              setConfirmDelete(false);
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
        {chosen && chosen.installed && !progress[chosen.name] && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => removeModel(chosen)}
              description={
                chosen.name === active
                  ? "This is your current model. You can download it again later."
                  : "You can download it again later."
              }
            >
              {confirmDelete ? `Tap again to delete ${chosen.name}` : `Delete ${chosen.name}`}
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

        </>
      )}

      <GroupHeader label="Testing" summary={phaseText} open={open === "testing"} onToggle={() => toggleGroup("testing")} />
      {open === "testing" && (
        <>
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
            disabled={!ready || phase === "starting" || phase === "transcribing" || phase === "typing"}
            description={
              startError || (!ready ? "Select a downloaded model first" : timing || undefined)
            }
          >
            {phase === "idle" && "Start recording"}
            {phase === "starting" && "Starting microphone... wait"}
            {phase === "recording" &&
              (dict.activator_held ? "SPEAK NOW - release the activator to stop" : "SPEAK NOW - tap to stop")}
            {phase === "transcribing" && "Transcribing..."}
            {phase === "typing" && "Typing..."}
          </ButtonItem>
        </PanelSectionRow>
        {last?.typed && (
          <PanelSectionRow>
            <Field label="Typing">{typedText(last.typed)}</Field>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <Field label="Text">
            <div style={{ whiteSpace: "pre-wrap", textAlign: "right" }}>{resultText || "-"}</div>
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={runTypingTest}
            description={typeMsg || "Types a short test sentence after 5 seconds, with your before and after keys, so you can click into any text box first"}
          >
            Test typing
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

        </>
      )}

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

export default definePlugin(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const input = (typeof SteamClient !== "undefined" && (SteamClient as any)?.Input) || null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let reg: any = null;

  const unregister = () => {
    try {
      if (typeof reg === "function") reg();
      else reg?.unregister?.();
    } catch {
      /* ignore */
    }
    reg = null;
  };
  const register = () => {
    unregister();
    if (typeof input?.RegisterForControllerInputMessages !== "function") return;
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      reg = input.RegisterForControllerInputMessages((...args: any[]) => {
        try {
          if (Array.isArray(args[0])) {
            for (const e of args[0]) if (typeof e?.nA === "number") uiButton(e.nA, !!e.bS);
          } else if (typeof args[1] === "number") {
            uiButton(args[1], !!args[2]);
          }
        } catch {
          /* the Steam callback swallows exceptions; never let one break the feed */
        }
      });
    } catch (e) {
      void logBackend(`controller feed failed: ${e}`);
    }
  };
  register();

  // The backend asks the Steam interface to type (the "Steam keyboard" method).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const onTypeText = (payload: any) => {
    let ok = false;
    try {
      if (typeof input?.ControllerKeyboardSendText === "function" && typeof payload?.text === "string") {
        input.ControllerKeyboardSendText(payload.text);
        ok = true;
      }
    } catch (e) {
      void logBackend(`ControllerKeyboardSendText failed: ${e}`);
    }
    void typeAck(String(payload?.id ?? ""), ok);
  };
  addEventListener("type_text", onTypeText);

  // Tell the backend which game / app is in front. No polling: Steam announces when an app
  // starts or stops, and the backend asks (query_app) each time you start dictating, which
  // also covers switching between apps that are already running.
  let lastApp: string | null = null;
  const reportApp = () => {
    try {
      const app = Router?.MainRunningApp;
      const id = app?.appid ? String(app.appid) : "";
      if (id !== lastApp) {
        lastApp = id;
        void setRunningApp(id, id ? String(app?.display_name ?? "") : "");
      }
    } catch {
      /* Router may not exist on this Steam build */
    }
  };
  const answerQuery = () => { lastApp = null; reportApp(); };
  addEventListener("query_app", answerQuery);
  let lifetime: { unregister?: () => void } | null = null;
  try {
    lifetime = SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(() => {
      reportApp();
      setTimeout(reportApp, 1500); // the running-app list can update just after the notification
    }) ?? null;
  } catch (e) {
    void logBackend(`app lifetime feed failed: ${e}`);
  }
  reportApp();

  // The feed stops after the machine sleeps; a long gap between ticks means it did.
  let lastTick = Date.now();
  const resume = setInterval(() => {
    const now = Date.now();
    const gap = now - lastTick;
    lastTick = now;
    if (gap > 30000) {
      uiHeld.clear();
      pushUi();
      register();
    }
  }, 10000);

  return {
    name: "Ubermoc Speech",
    titleView: <div>Ubermoc Speech</div>,
    content: (
      <Boundary>
        <Panel />
      </Boundary>
    ),
    icon: <FaMicrophone />,
    onDismount() {
      clearInterval(resume);
      removeEventListener("query_app", answerQuery);
      try { lifetime?.unregister?.(); } catch { /* ignore */ }
      removeEventListener("type_text", onTypeText);
      unregister();
      uiHeld.clear();
      if (uiHeartbeat) clearInterval(uiHeartbeat);
      uiHeartbeat = null;
    },
  };
});
