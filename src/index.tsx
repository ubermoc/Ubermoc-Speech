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
interface Profile { name: string; before_key: KeySpec; after_key: KeySpec }
interface Settings {
  model: string;
  language: string;
  autostart: boolean;
  indicator: boolean;
  rumble: boolean;
  punctuation: "auto" | "spoken";
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
interface LibApp { appid: string; name: string }
interface DictResult { ok: boolean; text?: string; error?: string; at: number }
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
const feeds = { state: false, messages: false, pad: false, keys: false, haptics: false };
let lastController = 0;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function steamInput(): any {
  return (typeof SteamClient !== "undefined" && SteamClient?.Input) || null;
}

function runningAppId(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const app = (Router as any).MainRunningApp;
  return app?.appid ? String(app.appid) : "";
}

function runningAppName(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const app = (Router as any).MainRunningApp;
  return app?.display_name ? String(app.display_name) : "";
}

function effective(s: Settings, appid: string): Settings {
  const p = s.profiles?.[appid];
  return p ? { ...s, before_key: p.before_key, after_key: p.after_key } : s;
}

// ---------- installed games (Steam and non-Steam kept separate) ----------
const APP_GAME = 1;
const APP_APPLICATION = 2;
const APP_SHORTCUT = 1073741824;

function installedApps(): { steam: LibApp[]; other: LibApp[] } {
  const steam: LibApp[] = [];
  const other: LibApp[] = [];
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = window as any;
    const rawAll = w.appStore?.allApps;
    const rawLocal = w.collectionStore?.localGamesCollection?.allApps;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const all: any[] =
      Array.isArray(rawAll) && rawAll.length ? rawAll : Array.isArray(rawLocal) ? rawLocal : [];
    const localIds = new Set<number>(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (Array.isArray(rawLocal) ? rawLocal : []).map((a: any) => a?.appid)
    );
    for (const a of all) {
      if (!a?.appid || !a?.display_name) continue;
      const entry = { appid: String(a.appid), name: String(a.display_name) };
      const isShortcut = a.app_type === APP_SHORTCUT || a.BIsShortcut?.() === true;
      if (isShortcut) {
        other.push(entry); // non-Steam shortcuts only exist if you added them
        continue;
      }
      if (a.app_type !== APP_GAME && a.app_type !== APP_APPLICATION) continue;
      const installed =
        localIds.has(a.appid) ||
        a.local_per_client_data?.installed === true ||
        a.selected_per_client_data?.installed === true;
      if (installed) steam.push(entry);
    }
  } catch (e) {
    void logBackend(`installedApps failed: ${e}`);
  }
  const byName = (x: LibApp, y: LibApp) => x.name.localeCompare(y.name);
  return { steam: steam.sort(byName), other: other.sort(byName) };
}

// ---------- cues: popup + rumble ----------
let activeToast: ReturnType<typeof toaster.toast> | undefined;

function dismissToast() {
  try {
    activeToast?.dismiss();
  } catch {
    /* ignore */
  }
  activeToast = undefined;
}

function showToast(body: string, duration: number) {
  if (!current?.indicator) return;
  dismissToast();
  try {
    activeToast = toaster.toast({
      title: "Ubermoc Speech",
      body,
      duration,
      playSound: false,
      showNewIndicator: false,
    });
  } catch (e) {
    void logBackend(`toast failed: ${e}`);
  }
}

function errorToast(body: string) {
  dismissToast();
  toaster.toast({ title: "Ubermoc Speech", body, duration: 5000 });
}

// Rumble: tries the browser Gamepad vibration actuator (real motors) and
// Steam's haptic pulse (trackpad haptics on a Deck). Whichever the system
// supports will fire; others are skipped.
function pulse(ms: number) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pads: any[] = Array.from((navigator.getGamepads?.() ?? []) as any);
  for (const pad of pads) {
    try {
      pad?.vibrationActuator?.playEffect?.("dual-rumble", {
        startDelay: 0,
        duration: ms,
        weakMagnitude: 0.4,
        strongMagnitude: 0.15,
      });
    } catch {
      /* ignore */
    }
  }
  const input = steamInput();
  if (input && typeof input.TriggerHapticPulse === "function") {
    for (let t = 0; t < ms; t += 30) {
      setTimeout(() => {
        try {
          input.TriggerHapticPulse(lastController, 0, 2500);
          input.TriggerHapticPulse(lastController, 1, 2500);
        } catch {
          /* ignore */
        }
      }, t);
    }
  }
}

function rumbleCue(kind: "start" | "end", force = false) {
  if (!force && !current?.rumble) return;
  if (kind === "start") pulse(160);
  else {
    pulse(90);
    setTimeout(() => pulse(90), 220); // two short pulses = finished
  }
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

// ---------- dictation state machine ----------
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
  rumbleCue("start");
  showToast("Listening...", 120000);
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
  rumbleCue("end");
  showToast("Transcribing...", 60000);
  const r = await stopAndTranscribe();
  setPhase("idle");
  const result: DictResult = { ok: !!(r.ok && r.text), text: r.text, error: r.error, at: Date.now() };
  if (!result.ok) {
    dismissToast();
    if (r.error && r.error !== "no audio captured") errorToast(r.error);
    else showToast("Didn't catch anything", 2500);
    testSink?.(result);
    return;
  }
  if (testSink) {
    showToast(`Heard: ${r.text}`.slice(0, 120), 4000);
    testSink(result);
    return;
  }
  showToast(`Typing: ${r.text}`.slice(0, 120), 2500);
  if (current) await deliver(r.text as string, effective(current, runningAppId()));
}

// ---------- input feeds ----------
const heldIds = new Set<number>();
let lastMask: Combo = { lower: 0, upper: 0 };
let held = false;

// While set, input is reported here instead of triggering dictation
// (used by "Record combo").
let captureCb: (() => void) | null = null;
// Live monitor shown in the panel to help diagnose button detection.
let monitorCb: ((text: string) => void) | null = null;

function monitorText(): string {
  return (
    `Pressed: ${describePressed(heldIds)}   ` +
    `[state ${lastMask.upper.toString(16)}:${lastMask.lower.toString(16)}]`
  );
}

function evaluate() {
  monitorCb?.(monitorText());
  if (captureCb) {
    captureCb();
    return;
  }
  if (!current) return;
  const down = comboHeld(lastMask, heldIds, current.combo ?? DEFAULT_COMBO);
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
  lastController = Number(st.unControllerIndex ?? lastController) || 0;
  lastMask = {
    lower: Number(st.ulButtons ?? 0) >>> 0,
    upper: Number(st.ulUpperButtons ?? 0) >>> 0,
  };
  evaluate();
}

function onInputMessage(controller: number, button: number, pressed: boolean) {
  lastController = controller;
  if (pressed) heldIds.add(button);
  else heldIds.delete(button);
  evaluate();
}

// Third feed: the standard browser Gamepad API (ids offset by 1000).
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

// ---------- UI pieces ----------
const MODE_OPTIONS = [
  { data: "enter", label: "Enter" },
  { data: "none", label: "None" },
  { data: "custom", label: "Choose key / combination" },
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
            <Field label="Will press" description="Pick from the lists above, or record from a keyboard">
              {describeKey(v)}
            </Field>
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

function OverrideEntry(props: {
  appid: string;
  profile: Profile;
  onChange: (p: Profile) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const p = props.profile;
  return (
    <>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setOpen(!open)}>
          {open ? "v " : "> "}
          {p.name || `App ${props.appid}`}
        </ButtonItem>
      </PanelSectionRow>
      {open && (
        <>
          <KeyBind label="Press before typing" value={p.before_key}
            onChange={(k) => props.onChange({ ...p, before_key: k })} />
          <KeyBind label="Press after typing" value={p.after_key}
            onChange={(k) => props.onChange({ ...p, after_key: k })} />
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={props.onRemove}>
              Remove override
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
  const [overridesOpen, setOverridesOpen] = useState(false);
  const [monitor, setMonitor] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [upd, setUpd] = useState<UpdateInfo | null>(null);
  const [updMsg, setUpdMsg] = useState("");
  const [updBusy, setUpdBusy] = useState(false);
  const [phaseNow, setPhaseNow] = useState<Phase>(phase);
  const [last, setLast] = useState<DictResult | null>(null);
  const [testMsg, setTestMsg] = useState("");
  const appid = runningAppId();

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
    // While this menu is open, dictation results show here instead of being typed.
    testSink = setLast;
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

  const update = async (patch: Partial<Settings>) => {
    const next: Settings = { ...settings, ...patch };
    setLocal(next);
    current = next;
    await setSettings(next);
  };

  const restartServerIfRunning = async () => {
    if (status?.running) {
      await stopServer();
      await startServer();
    }
    setStatus(await getStatus());
  };

  const setProfile = (id: string, p: Profile | null) => {
    const profiles = { ...settings.profiles };
    if (p) profiles[id] = p;
    else delete profiles[id];
    return update({ profiles });
  };

  const addOverride = (id: string, name: string) => {
    if (settings.profiles?.[id]) return;
    return setProfile(id, { name, before_key: settings.before_key, after_key: settings.after_key });
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
      if (!comboEmpty(combo)) void update({ combo });
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

  // ----- models -----
  const sel = models.find((m) => m.name === selected);
  const downloadSelected = async () => {
    if (!sel) return;
    setProgress({ ...progress, [sel.name]: 0 });
    const r = await downloadModel(sel.name);
    if (!r.ok) errorToast(`Download failed: ${r.error ?? ""}`);
    await refresh();
    if (r.ok) {
      await update({ model: sel.name }); // use the model that was just downloaded
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

  // ----- apps -----
  const apps = installedApps();
  const steamChoices = apps.steam.filter((a) => !settings.profiles?.[a.appid]);
  const otherChoices = apps.other.filter((a) => !settings.profiles?.[a.appid]);
  const profileEntries = Object.entries(settings.profiles ?? {});

  // ----- tests -----
  const runKeyTest = async () => {
    for (let i = 5; i > 0; i--) {
      setTestMsg(`Typing in ${i}... close this menu and click a text box`);
      await sleep(1000);
    }
    setTestMsg("Typing now...");
    await deliver("Ubermoc Speech test", effective(settings, runningAppId()));
    setTestMsg("Done. If nothing appeared, tell me what happened.");
  };

  const phaseText =
    phaseNow === "listening" ? "Listening..." : phaseNow === "transcribing" ? "Transcribing..." : "Idle";

  return (
    <>
      <PanelSection title="Global">
        <KeyBind label="Press before typing" value={settings.before_key}
          onChange={(k) => update({ before_key: k })} />
        <KeyBind label="Press after typing" value={settings.after_key}
          onChange={(k) => update({ after_key: k })} />
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={recordCombo} disabled={capturing}
            description="Controller buttons you hold while you talk">
            {capturing
              ? "Listening for CONTROLLER buttons... hold them together, then let go"
              : `Hold-to-talk buttons: ${describeCombo(settings.combo)}  (tap to change)`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => update({ combo: DEFAULT_COMBO })}>
            Reset hold-to-talk to the two right back buttons
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Punctuation"
            description={
              settings.punctuation === "spoken"
                ? 'Only types punctuation you say out loud, e.g. "comma", "period", "question mark", "new line".'
                : 'Whisper decides where punctuation goes. Saying "period" types the word.'
            }
            rgOptions={[
              { data: "auto", label: "Automatic (Whisper adds it)" },
              { data: "spoken", label: "Spoken (I say it)" },
            ]}
            selectedOption={settings.punctuation ?? "auto"}
            onChange={(o) => update({ punctuation: o.data as Settings["punctuation"] })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Show popup while listening"
            description="A Steam notification says Listening / Transcribing"
            checked={settings.indicator}
            onChange={(v) => update({ indicator: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Light rumble"
            description="One pulse when listening starts, two when it ends"
            checked={settings.rumble}
            onChange={(v) => update({ rumble: v })}
          />
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

      <PanelSection title="Game & app overrides">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setOverridesOpen(!overridesOpen)}>
            {overridesOpen ? "v " : "> "}
            {profileEntries.length
              ? `${profileEntries.length} override${profileEntries.length > 1 ? "s" : ""}`
              : "No overrides - tap to add"}
          </ButtonItem>
        </PanelSectionRow>
        {overridesOpen && (
          <>
            {appid && !settings.profiles?.[appid] && (
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={() => addOverride(appid, runningAppName())}>
                  Add running game{runningAppName() ? `: ${runningAppName()}` : ""}
                </ButtonItem>
              </PanelSectionRow>
            )}
            <PanelSectionRow>
              <DropdownItem
                label="Add installed Steam game"
                description={`${apps.steam.length} installed`}
                disabled={steamChoices.length === 0}
                rgOptions={steamChoices.map((a) => ({ data: a.appid, label: a.name }))}
                selectedOption={undefined}
                strDefaultLabel={steamChoices.length ? "Choose a game..." : "None available"}
                onChange={(o) => {
                  const a = steamChoices.find((x) => x.appid === o.data);
                  if (a) void addOverride(a.appid, a.name);
                }}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Add non-Steam game or app"
                description={`${apps.other.length} found`}
                disabled={otherChoices.length === 0}
                rgOptions={otherChoices.map((a) => ({ data: a.appid, label: a.name }))}
                selectedOption={undefined}
                strDefaultLabel={otherChoices.length ? "Choose an app..." : "None available"}
                onChange={(o) => {
                  const a = otherChoices.find((x) => x.appid === o.data);
                  if (a) void addOverride(a.appid, a.name);
                }}
              />
            </PanelSectionRow>
            {profileEntries.map(([id, p]) => (
              <OverrideEntry
                key={id}
                appid={id}
                profile={p}
                onChange={(np) => setProfile(id, np)}
                onRemove={() => setProfile(id, null)}
              />
            ))}
          </>
        )}
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
            onChange={(v) => update({ autostart: v })}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Models">
        {models.map((m) => (
          <PanelSectionRow key={m.name}>
            <ButtonItem
              layout="below"
              description={`${sizeLabel(m.size_mb)} - ${m.description}`}
              onClick={() => {
                setSelected(m.name);
                setConfirmDelete(false);
              }}
            >
              {m.name === selected ? "● " : "○ "}
              {m.name}
              {m.installed ? "  (installed)" : ""}
              {m.installed && m.name === settings.model ? "  (in use)" : ""}
            </ButtonItem>
          </PanelSectionRow>
        ))}
        {sel && (
          <>
            {progress[sel.name] !== undefined && (
              <PanelSectionRow>
                <ProgressBarWithInfo
                  label={`Downloading ${sel.name}`}
                  nProgress={progress[sel.name] * 100}
                  sOperationText={`${Math.round(progress[sel.name] * 100)}%`}
                />
              </PanelSectionRow>
            )}
            {!sel.installed && progress[sel.name] === undefined && (
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={downloadSelected}
                  description="Downloaded once and kept permanently">
                  Download {sel.name} ({sizeLabel(sel.size_mb)})
                </ButtonItem>
              </PanelSectionRow>
            )}
            {sel.installed && sel.name !== settings.model && (
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={async () => {
                    await update({ model: sel.name });
                    await restartServerIfRunning();
                  }}
                >
                  Use {sel.name}
                </ButtonItem>
              </PanelSectionRow>
            )}
            {sel.installed && (
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={deleteSelected}>
                  {confirmDelete ? `Tap again to delete ${sel.name}` : `Delete ${sel.name}`}
                </ButtonItem>
              </PanelSectionRow>
            )}
          </>
        )}
      </PanelSection>

      <PanelSection title="Test">
        <PanelSectionRow>
          <Field label="Status" description="While this menu is open, dictation results appear below instead of being typed">
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
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={runKeyTest}
            description={testMsg || "Presses your before key, pastes a test sentence, then your after key"}>
            Type a test sentence in 5 seconds
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" description={`Haptics available: ${feeds.haptics ? "yes" : "unknown"}`}
            onClick={() => {
              rumbleCue("start", true);
              setTimeout(() => rumbleCue("end", true), 800);
              showToast("Rumble test", 2000);
            }}>
            Test popup and rumble
          </ButtonItem>
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
  feeds.haptics = typeof input.TriggerHapticPulse === "function";
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
      dismissToast();
      captureCb = null;
      monitorCb = null;
    },
  };
});
