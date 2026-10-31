import { callable, toaster } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ProgressBarWithInfo,
  Router,
  ToggleField,
  definePlugin,
} from "@decky/ui";
import { Component, ReactNode, useEffect, useState } from "react";
import { FaMicrophone } from "react-icons/fa";
import {
  Combo,
  DEFAULT_COMBO,
  comboEmpty,
  comboHeld,
  describeCombo,
  describePressed,
  recordableSteamId,
} from "./buttons";
import {
  KEY_CTRL,
  KEY_ENTER,
  KEY_OPTIONS,
  KEY_V,
  KeySpec,
  MOD_OPTIONS,
  describeKey,
  hidFromEvent,
  modsFromEvent,
  modsIndex,
} from "./keys";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const SteamClient: any;

// ---------- types ----------
interface Profile {
  name: string;
  combo?: Combo;
  before_key: KeySpec;
  after_key: KeySpec;
}
interface Settings {
  model: string;
  language: string;
  autostart: boolean;
  combo: Combo;
  before_key: KeySpec;
  after_key: KeySpec;
  profiles: Record<string, Profile>;
}
interface ModelInfo { name: string; size_mb: number; description: string; installed: boolean }
interface Status { running: boolean; recording: boolean; binary: boolean }
interface BuildInfo { version: string; build: string }
interface UpdateInfo {
  ok: boolean;
  installed: BuildInfo;
  latest?: BuildInfo;
  available?: boolean;
  error?: string;
}
interface DictResult { ok: boolean; text?: string; error?: string }
type Phase = "idle" | "listening" | "transcribing";

// ---------- backend ----------
const getSettings = callable<[], Settings>("get_settings");
const setSettings = callable<[Settings], Settings>("set_settings");
const listModels = callable<[], ModelInfo[]>("list_models");
const downloadModel = callable<[string], { ok: boolean; error?: string }>("download_model");
const getProgress = callable<[], Record<string, number>>("get_download_progress");
const deleteModel = callable<[string], boolean>("delete_model");
const startServer = callable<[], { ok: boolean; error?: string }>("start_server");
const stopServer = callable<[], { ok: boolean }>("stop_server");
const getStatus = callable<[], Status>("status");
const startRecording = callable<[], { ok: boolean; error?: string }>("start_recording");
const stopAndTranscribe = callable<[], { ok: boolean; text?: string; error?: string }>(
  "stop_and_transcribe"
);
const setClipboard = callable<[string], { ok: boolean; method: string }>("set_clipboard");
const checkUpdate = callable<[], UpdateInfo>("check_update");
const installUpdate = callable<[], { ok: boolean; version?: string; error?: string }>("install_update");
const logBackend = callable<[string], boolean>("log");

// ---------- module state (works with the panel closed) ----------
let current: Settings | null = null;
const feeds = { state: false, messages: false, pad: false, keys: false };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function steamInput(): any {
  return (typeof SteamClient !== "undefined" && SteamClient?.Input) || null;
}

function runningApp(): { id: string; name: string } {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const app = (Router as any).MainRunningApp;
  return { id: app?.appid ? String(app.appid) : "", name: app?.display_name ? String(app.display_name) : "" };
}

// A per-game profile overrides the global values; anything it lacks falls back.
function effective(s: Settings, appid: string): Settings {
  const p = s.profiles?.[appid];
  if (!p) return s;
  return {
    ...s,
    combo: p.combo ?? s.combo,
    before_key: p.before_key ?? s.before_key,
    after_key: p.after_key ?? s.after_key,
  };
}

function errorToast(body: string) {
  toaster.toast({ title: "Ubermoc Speech", body, duration: 5000 });
}

// ---------- key output ----------
async function tapKeys(mods: number[], code: number) {
  const input = steamInput();
  if (typeof input?.ControllerKeyboardSetKeyState !== "function") {
    errorToast("This Steam build cannot send key presses");
    return;
  }
  for (const m of mods) input.ControllerKeyboardSetKeyState(m, true);
  if (mods.length) await sleep(25);
  input.ControllerKeyboardSetKeyState(code, true);
  await sleep(45);
  input.ControllerKeyboardSetKeyState(code, false);
  if (mods.length) await sleep(25);
  for (const m of [...mods].reverse()) input.ControllerKeyboardSetKeyState(m, false);
  await sleep(45);
}

async function pressSpec(k: KeySpec) {
  if (k.mode === "none") return;
  if (k.mode === "enter") return tapKeys([], KEY_ENTER);
  return tapKeys(k.mods ?? [], k.code);
}

async function deliver(text: string, s: Settings) {
  const res = await setClipboard(text);
  let clipboardOk = res.ok;
  if (!clipboardOk) {
    try {
      await navigator.clipboard.writeText(text);
      clipboardOk = true;
    } catch {
      clipboardOk = false;
    }
  }
  await pressSpec(s.before_key);
  if (clipboardOk) {
    await tapKeys([KEY_CTRL], KEY_V); // paste
    await sleep(80);
  } else {
    // No clipboard available: have Steam type the text directly.
    const input = steamInput();
    if (typeof input?.ControllerKeyboardSendText === "function") {
      input.ControllerKeyboardSendText(text);
      await sleep(120);
    } else {
      errorToast("Could not paste or type the text");
      return;
    }
  }
  await pressSpec(s.after_key);
}

// ---------- dictation ----------
let phase: Phase = "idle";
const phaseSubs = new Set<(p: Phase) => void>();
let testSink: ((r: DictResult) => void) | null = null;
let startPending: Promise<{ ok: boolean; error?: string }> | null = null;

function setPhase(p: Phase) {
  phase = p;
  phaseSubs.forEach((f) => f(p));
}

async function beginDictation(): Promise<void> {
  if (!current || phase !== "idle") return;
  setPhase("listening");
  startPending = startRecording();
  const r = await startPending;
  startPending = null;
  if (!r.ok) {
    setPhase("idle");
    errorToast(r.error ?? "Recording failed");
  }
}

async function endDictation(): Promise<void> {
  if (phase !== "listening") return;
  if (startPending) await startPending;
  if (phase !== "listening") return;
  setPhase("transcribing");
  const r = await stopAndTranscribe();
  setPhase("idle");
  const result: DictResult = { ok: !!(r.ok && r.text), text: r.text, error: r.error };
  if (testSink) {
    testSink(result); // menu is open: show the result instead of typing it
    return;
  }
  if (!result.ok) {
    if (r.error && r.error !== "no audio captured") errorToast(r.error);
    return;
  }
  if (current) await deliver(r.text as string, effective(current, runningApp().id));
}

// ---------- controller input ----------
const heldIds = new Set<number>();
let lastMask: Combo = { lower: 0, upper: 0 };
let held = false;

// While set, input is reported here instead of triggering dictation
// (used while recording a combo).
let captureCb: (() => void) | null = null;
// Live monitor shown in the panel to help diagnose button detection.
let monitorCb: ((text: string) => void) | null = null;

function evaluate() {
  monitorCb?.(
    `Pressed: ${describePressed(heldIds)}   [state ${lastMask.upper.toString(16)}:${lastMask.lower.toString(16)}]`
  );
  if (captureCb) {
    captureCb();
    return;
  }
  if (!current) return;
  const combo = effective(current, runningApp().id).combo ?? DEFAULT_COMBO;
  const down = comboHeld(lastMask, heldIds, combo);
  if (down && !held) {
    held = true;
    void beginDictation();
  } else if (!down && held) {
    held = false;
    void endDictation();
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function onControllerState(states: any[]) {
  const st = states?.[0];
  if (!st) return;
  lastMask = {
    lower: Number(st.ulButtons ?? 0) >>> 0,
    upper: Number(st.ulUpperButtons ?? 0) >>> 0,
  };
  evaluate();
}

function onInputMessage(_controller: number, button: number, pressed: boolean) {
  if (pressed) heldIds.add(button);
  else heldIds.delete(button);
  evaluate();
}

// Extra feed: the standard browser Gamepad API (ids offset by 1000).
let padPrev = new Set<number>();
function pollGamepads() {
  const pads = navigator.getGamepads?.() ?? [];
  const now = new Set<number>();
  for (const pad of pads) {
    pad?.buttons.forEach((b, i) => {
      if (b.pressed) now.add(1000 + i);
    });
  }
  let changed = false;
  now.forEach((i) => {
    if (!padPrev.has(i)) {
      heldIds.add(i);
      changed = true;
    }
  });
  padPrev.forEach((i) => {
    if (!now.has(i)) {
      heldIds.delete(i);
      changed = true;
    }
  });
  padPrev = now;
  if (changed) evaluate();
}

// ---------- UI ----------
const MODE_OPTIONS = [
  { data: "enter", label: "Enter" },
  { data: "none", label: "None" },
  { data: "custom", label: "Key / combination" },
];

function KeyBind(props: {
  label: string;
  value: KeySpec;
  onChange: (k: KeySpec) => void;
}) {
  const [recording, setRecording] = useState(false);
  const v = props.value;

  useEffect(() => {
    if (!recording) return;
    const handler = (e: KeyboardEvent) => {
      const code = hidFromEvent(e);
      if (code === undefined) return; // modifier on its own: wait for the key
      e.preventDefault();
      e.stopPropagation();
      props.onChange({ mode: "custom", code, mods: modsFromEvent(e) });
      setRecording(false);
    };
    window.addEventListener("keydown", handler, true);
    const timeout = setTimeout(() => setRecording(false), 15000);
    return () => {
      window.removeEventListener("keydown", handler, true);
      clearTimeout(timeout);
    };
  }, [recording]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <PanelSectionRow>
        <DropdownItem
          label={props.label}
          rgOptions={MODE_OPTIONS}
          selectedOption={v.mode}
          onChange={(o) =>
            props.onChange({
              mode: o.data as KeySpec["mode"],
              code: v.code || KEY_ENTER,
              mods: v.mods ?? [],
            })
          }
        />
      </PanelSectionRow>
      {v.mode === "custom" && (
        <>
          <PanelSectionRow>
            <DropdownItem
              label="Hold with"
              rgOptions={MOD_OPTIONS.map((m, i) => ({ data: i, label: m.label }))}
              selectedOption={modsIndex(v.mods)}
              onChange={(o) => props.onChange({ ...v, mods: MOD_OPTIONS[o.data as number].mods })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem
              label="Key"
              rgOptions={KEY_OPTIONS.map((k) => ({ data: k.code, label: k.label }))}
              selectedOption={v.code}
              onChange={(o) => props.onChange({ ...v, code: o.data as number })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Will press">{describeKey(v)}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => setRecording(!recording)}>
              {recording
                ? "Listening for a KEYBOARD key... press it now (15 s)"
                : "Record from a keyboard (optional)"}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </>
  );
}

const sizeLabel = (mb: number) => (mb >= 1000 ? `~${(mb / 1000).toFixed(1)} GB` : `~${mb} MB`);

function Panel() {
  const [settings, setLocal] = useState<Settings | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [status, setStatus] = useState<Status | null>(null);
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [capturing, setCapturing] = useState(false);
  const [monitor, setMonitor] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [upd, setUpd] = useState<UpdateInfo | null>(null);
  const [updMsg, setUpdMsg] = useState("");
  const [updBusy, setUpdBusy] = useState(false);
  const [phaseNow, setPhaseNow] = useState<Phase>(phase);
  const [last, setLast] = useState<DictResult | null>(null);
  const app = runningApp();

  const refresh = async () => {
    setModels(await listModels());
    setStatus(await getStatus());
  };

  useEffect(() => {
    void getSettings().then((s) => {
      setLocal(s);
      setSelected(s.model);
    });
    void refresh();
    void checkUpdate().then(setUpd);
    phaseSubs.add(setPhaseNow);
    testSink = setLast; // while this menu is open, results show here
    const t = setInterval(async () => {
      setProgress(await getProgress());
      setStatus(await getStatus());
    }, 1500);
    return () => {
      clearInterval(t);
      phaseSubs.delete(setPhaseNow);
      testSink = null;
      monitorCb = null;
      captureCb = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    monitorCb = monitor === null ? null : setMonitor;
    return () => {
      monitorCb = null;
    };
  }, [monitor === null]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!settings) return <PanelSection title="Ubermoc Speech">Loading...</PanelSection>;

  const hasProfile = !!(app.id && settings.profiles?.[app.id]);
  const view = effective(settings, app.id);

  const saveAll = async (next: Settings) => {
    setLocal(next);
    current = next;
    await setSettings(next);
  };

  // Edits go to the running game's profile when it has one, otherwise to the globals.
  const editBinds = (patch: Partial<Pick<Settings, "combo" | "before_key" | "after_key">>) => {
    if (hasProfile) {
      const profiles = { ...settings.profiles, [app.id]: { ...settings.profiles[app.id], ...patch } };
      return saveAll({ ...settings, profiles });
    }
    return saveAll({ ...settings, ...patch });
  };

  const toggleProfile = (on: boolean) => {
    const profiles = { ...settings.profiles };
    if (on) {
      profiles[app.id] = {
        name: app.name || `App ${app.id}`,
        combo: settings.combo,
        before_key: settings.before_key,
        after_key: settings.after_key,
      };
    } else delete profiles[app.id];
    return saveAll({ ...settings, profiles });
  };

  const restartServerIfRunning = async () => {
    if (status?.running) {
      await stopServer();
      await startServer();
    }
    setStatus(await getStatus());
  };

  // ----- hold-to-talk recording -----
  const recordCombo = () => {
    setCapturing(true);
    const seenSteam = new Set<number>();
    const seenPad = new Set<number>();
    const seenMask: Combo = { lower: 0, upper: 0 };
    const baseMask = { ...lastMask };
    let armed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = () => {
      captureCb = null;
      setCapturing(false);
      let combo: Combo;
      if (seenSteam.size) combo = { lower: 0, upper: 0, ids: [...seenSteam] };
      else if (seenPad.size) combo = { lower: 0, upper: 0, ids: [...seenPad] };
      else
        combo = {
          lower: seenMask.lower & ~baseMask.lower,
          upper: seenMask.upper & ~baseMask.upper,
          ids: [],
        };
      if (!comboEmpty(combo)) void editBinds({ combo });
      else errorToast("No button input detected - turn on 'Live input' to troubleshoot");
    };
    timer = setTimeout(finish, 10000); // give up after 10 s of silence
    captureCb = () => {
      const steam = [...heldIds].filter((i) => i < 1000 && recordableSteamId(i));
      const pad = [...heldIds].filter((i) => i >= 1000);
      // Wait until the button that was used to start recording is released.
      if (!armed) {
        if (steam.length === 0 && pad.length === 0) armed = true;
        return;
      }
      if (!steam.length && !pad.length && !lastMask.lower && !lastMask.upper) return;
      steam.forEach((i) => seenSteam.add(i));
      pad.forEach((i) => seenPad.add(i));
      seenMask.lower |= lastMask.lower;
      seenMask.upper |= lastMask.upper;
      clearTimeout(timer);
      timer = setTimeout(finish, 800); // finish shortly after input settles
    };
  };

  // ----- models: select one, then download / delete it -----
  const sel = models.find((m) => m.name === selected);
  const selectModel = async (m: ModelInfo) => {
    setSelected(m.name);
    setConfirmDelete(false);
    if (m.installed && m.name !== settings.model) {
      await saveAll({ ...settings, model: m.name }); // an installed model becomes the one in use
      await restartServerIfRunning();
    }
  };
  const downloadSelected = async () => {
    if (!sel) return;
    setProgress({ ...progress, [sel.name]: 0 });
    const r = await downloadModel(sel.name);
    if (!r.ok) errorToast(`Download failed: ${r.error ?? ""}`);
    await refresh();
    if (r.ok) {
      await saveAll({ ...settings, model: sel.name });
      if (!status?.running && settings.autostart) await startServer();
      else await restartServerIfRunning();
      setStatus(await getStatus());
    }
  };
  const deleteSelected = async () => {
    if (!sel) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    setConfirmDelete(false);
    if (sel.name === settings.model) await stopServer();
    await deleteModel(sel.name);
    await refresh();
  };

  const phaseText =
    phaseNow === "listening" ? "Listening..." : phaseNow === "transcribing" ? "Transcribing..." : "Idle";

  return (
    <>
      <PanelSection title="Global">
        {app.id && (
          <PanelSectionRow>
            <ToggleField
              label="Use per-game profile"
              description={`${app.name || `App ${app.id}`}: its own buttons and keys instead of the global ones`}
              checked={hasProfile}
              onChange={toggleProfile}
            />
          </PanelSectionRow>
        )}
        <KeyBind label="Press before typing" value={view.before_key}
          onChange={(k) => editBinds({ before_key: k })} />
        <KeyBind label="Press after typing" value={view.after_key}
          onChange={(k) => editBinds({ after_key: k })} />
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={recordCombo} disabled={capturing}
            description="Controller buttons you hold while you talk">
            {capturing
              ? "Listening for CONTROLLER buttons... hold them together, then let go"
              : `Hold-to-talk buttons: ${describeCombo(view.combo)}  (tap to change)`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => editBinds({ combo: DEFAULT_COMBO })}>
            Reset hold-to-talk to the two right back buttons
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Live input"
            description={
              monitor ??
              `Feeds - Steam buttons: ${feeds.messages ? "yes" : "NO"}, Steam state: ${feeds.state ? "yes" : "NO"}, gamepad: ${feeds.pad ? "yes" : "NO"}, key output: ${feeds.keys ? "yes" : "NO"}`
            }
            checked={monitor !== null}
            onChange={(on) => setMonitor(on ? "waiting for input..." : null)}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Server">
        <PanelSectionRow>
          <ToggleField
            label="Start server"
            description={
              status && !status.binary
                ? "whisper-server binary missing - reinstall the plugin"
                : "Necessary for dictation to work"
            }
            checked={!!status?.running}
            onChange={async (on) => {
              const r = on ? await startServer() : await stopServer();
              if (!r.ok) errorToast((r as { error?: string }).error ?? "Failed");
              setStatus(await getStatus());
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Start on boot"
            checked={settings.autostart}
            onChange={(v) => saveAll({ ...settings, autostart: v })}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Models">
        {models.map((m) => (
          <PanelSectionRow key={m.name}>
            <ButtonItem
              layout="below"
              description={`${sizeLabel(m.size_mb)} - ${m.description}`}
              onClick={() => selectModel(m)}
            >
              {m.name === selected ? "● " : "○ "}
              {m.name}
              {m.installed ? "  (installed)" : ""}
              {m.installed && m.name === settings.model ? "  (in use)" : ""}
            </ButtonItem>
          </PanelSectionRow>
        ))}
        {sel && progress[sel.name] !== undefined && (
          <PanelSectionRow>
            <ProgressBarWithInfo
              label={`Downloading ${sel.name}`}
              nProgress={progress[sel.name] * 100}
              sOperationText={`${Math.round(progress[sel.name] * 100)}%`}
            />
          </PanelSectionRow>
        )}
        {sel && !sel.installed && progress[sel.name] === undefined && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={downloadSelected}
              description="Downloaded once and kept permanently">
              Download {sel.name} ({sizeLabel(sel.size_mb)})
            </ButtonItem>
          </PanelSectionRow>
        )}
        {sel && sel.installed && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={deleteSelected}>
              {confirmDelete ? `Tap again to delete ${sel.name}` : `Delete ${sel.name}`}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Test">
        <PanelSectionRow>
          <Field label="Status" description="While this menu is open, what you dictate appears below instead of being typed">
            {phaseText}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Or hold your hold-to-talk buttons"
            onClick={() => (phase === "listening" ? void endDictation() : void beginDictation())}
            disabled={phaseNow === "transcribing"}
          >
            {phaseNow === "listening" ? "Stop and transcribe" : "Start test recording"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Last result">
            <div style={{ whiteSpace: "pre-wrap", textAlign: "right" }}>
              {last ? (last.ok ? last.text : `(${last.error ?? "nothing heard"})`) : "-"}
            </div>
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

export default definePlugin(() => {
  void getSettings().then((s) => {
    current = s;
  });
  // Steam builds differ: only call input functions that actually exist, and
  // never let a missing one stop the plugin from loading.
  const input = steamInput() ?? {};
  const names: string[] = [];
  // eslint-disable-next-line guard-for-in
  for (const k in input) if (/Controller|Register|Key|Haptic/i.test(k)) names.push(k);
  void logBackend(`SteamClient.Input functions: ${names.join(",") || "(none)"}`);

  const tryRegister = (fn: string, cb: unknown) => {
    try {
      if (typeof input[fn] !== "function") return null;
      return input[fn](cb);
    } catch (e) {
      void logBackend(`${fn} failed: ${e}`);
      return null;
    }
  };
  const regState = tryRegister("RegisterForControllerStateChanges", onControllerState);
  const regMsg = tryRegister("RegisterForControllerInputMessages", onInputMessage);
  feeds.state = regState !== null;
  feeds.messages = regMsg !== null;
  feeds.pad = typeof navigator.getGamepads === "function";
  feeds.keys = typeof input.ControllerKeyboardSetKeyState === "function";
  const padTimer = setInterval(pollGamepads, 30);

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
      regState?.unregister?.();
      regMsg?.unregister?.();
      clearInterval(padTimer);
      captureCb = null;
      monitorCb = null;
    },
  };
});
