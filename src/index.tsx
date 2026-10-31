import { callable, toaster } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  ProgressBarWithInfo,
  Router,
  SliderField,
  ToggleField,
  definePlugin,
} from "@decky/ui";
import { Component, ReactNode, useEffect, useState } from "react";
import { FaMicrophone } from "react-icons/fa";
import { Combo, DEFAULT_COMBO, comboEmpty, comboHeld, describeCombo } from "./buttons";
import {
  KEY_CTRL,
  KEY_ENTER,
  KEY_V,
  KeySpec,
  MOD_CODES,
  describeKey,
  hidFromEvent,
} from "./keys";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const SteamClient: any;

// ---------- types ----------
interface Settings {
  model: string;
  language: string;
  threads: number;
  autostart: boolean;
  punctuation: "auto" | "spoken";
  combo: Combo;
  before_key: KeySpec;
  after_key: KeySpec;
  profiles: Record<string, Profile>;
}
interface Profile { name: string; before_key: KeySpec; after_key: KeySpec }
interface LibApp { appid: string; name: string }
interface ModelInfo { name: string; size_mb: number; installed: boolean }
interface Status { running: boolean; recording: boolean; binary: boolean }

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
interface UpdateInfo { ok: boolean; installed: string; latest?: string; available?: boolean; error?: string }
const checkUpdate = callable<[], UpdateInfo>("check_update");
const installUpdate = callable<[], { ok: boolean; version?: string; error?: string }>("install_update");
const logBackend = callable<[string], boolean>("log");
const setClipboard = callable<[string], { ok: boolean; method: string }>("set_clipboard");

// ---------- module state (works with the panel closed) ----------
let current: Settings | null = null;
let held = false;
let busy = false;

function runningAppId(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const app = (Router as any).MainRunningApp;
  return app?.appid ? String(app.appid) : "";
}

function effective(s: Settings, appid: string): Settings {
  const p = s.profiles?.[appid];
  return p ? { ...s, before_key: p.before_key, after_key: p.after_key } : s;
}

function libraryApps(): LibApp[] {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw = (window as any).appStore?.allApps;
  const all = Array.isArray(raw) ? raw : [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return all
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    .filter((a: any) => a?.appid && a?.display_name)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    .map((a: any) => ({ appid: String(a.appid), name: String(a.display_name) }))
    .sort((a: LibApp, b: LibApp) => a.name.localeCompare(b.name));
}

function runningAppName(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const app = (Router as any).MainRunningApp;
  return app?.display_name ? String(app.display_name) : "";
}

// ---------- key output ----------
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Which input features this Steam build actually provides (filled at load).
const feeds = { state: false, messages: false, pad: false, keys: false, names: "" };

async function tapKeys(mods: number[], code: number) {
  const input = SteamClient?.Input;
  if (typeof input?.ControllerKeyboardSetKeyState !== "function") {
    toaster.toast({ title: "Ubermoc Speech", body: "This Steam build cannot send key presses" });
    return;
  }
  for (const m of mods) input.ControllerKeyboardSetKeyState(m, true);
  input.ControllerKeyboardSetKeyState(code, true);
  await sleep(40);
  input.ControllerKeyboardSetKeyState(code, false);
  for (const m of [...mods].reverse()) input.ControllerKeyboardSetKeyState(m, false);
  await sleep(40);
}

async function pressSpec(k: KeySpec) {
  if (k.mode === "none") return;
  if (k.mode === "enter") return tapKeys([], KEY_ENTER);
  return tapKeys(k.mods ?? [], k.code);
}

async function deliver(text: string, s: Settings) {
  const res = await setClipboard(text);
  if (!res.ok) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      toaster.toast({ title: "Ubermoc Speech", body: "Could not set clipboard" });
      return;
    }
  }
  await pressSpec(s.before_key);
  await tapKeys([KEY_CTRL], KEY_V); // paste
  await sleep(80);
  await pressSpec(s.after_key);
}

// ---------- hold-to-talk ----------
async function onPress() {
  if (!current || busy) return;
  busy = true;
  const r = await startRecording();
  if (!r.ok) {
    busy = false;
    toaster.toast({ title: "Ubermoc Speech", body: r.error ?? "Recording failed" });
  }
}

async function onRelease() {
  if (!busy) return;
  const r = await stopAndTranscribe();
  busy = false;
  if (!current) return;
  if (!r.ok || !r.text) {
    if (r.error && r.error !== "no audio captured")
      toaster.toast({ title: "Ubermoc Speech", body: r.error });
    return;
  }
  await deliver(r.text, effective(current, runningAppId()));
}

// ---------- input feeds ----------
const heldIds = new Set<number>();
let lastMask: Combo = { lower: 0, upper: 0 };

// While set, input is reported here instead of triggering dictation
// (used by "Record combo").
let captureCb: ((mask: Combo, ids: Set<number>) => void) | null = null;
// Live monitor shown in the panel to help diagnose button detection.
let monitorCb: ((text: string) => void) | null = null;

function evaluate() {
  if (captureCb) {
    captureCb(lastMask, heldIds);
    return;
  }
  if (!current) return;
  const combo = current.combo ?? DEFAULT_COMBO;
  const down = comboHeld(lastMask, heldIds, combo);
  if (down && !held) {
    held = true;
    void onPress();
  } else if (!down && held) {
    held = false;
    void onRelease();
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
  monitorCb?.(
    `mask ${lastMask.upper.toString(16)}:${lastMask.lower.toString(16)}  ids [${[...heldIds].join(",")}]`
  );
  evaluate();
}

// Third feed: the standard browser Gamepad API. Ids are offset by 1000 so they
// never collide with Steam's own button ids.
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
  now.forEach((i) => { if (!padPrev.has(i)) { heldIds.add(i); changed = true; } });
  padPrev.forEach((i) => { if (!now.has(i)) { heldIds.delete(i); changed = true; } });
  padPrev = now;
  if (changed) {
    monitorCb?.(
      `mask ${lastMask.upper.toString(16)}:${lastMask.lower.toString(16)}  ids [${[...heldIds].join(",")}]`
    );
    evaluate();
  }
}

function onInputMessage(_controller: number, button: number, pressed: boolean) {
  if (pressed) heldIds.add(button);
  else heldIds.delete(button);
  monitorCb?.(
    `mask ${lastMask.upper.toString(16)}:${lastMask.lower.toString(16)}  ids [${[...heldIds].join(",")}]`
  );
  evaluate();
}

// ---------- UI ----------
const MODE_OPTIONS = [
  { data: "enter", label: "Enter" },
  { data: "none", label: "None" },
  { data: "custom", label: "Keyboard key / combination" },
];

function KeyPicker(props: {
  label: string;
  value: KeySpec;
  onChange: (k: KeySpec) => void;
}) {
  const [recording, setRecording] = useState(false);

  useEffect(() => {
    if (!recording) return;
    const mods = new Set<number>();
    const handler = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const code = hidFromEvent(e);
      if (code === undefined) return;
      if (MOD_CODES.has(code)) {
        mods.add(code); // wait for the main key of the combination
        return;
      }
      props.onChange({ mode: "custom", code, mods: [...mods] });
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
          selectedOption={props.value.mode}
          onChange={(o) => {
            const mode = o.data as KeySpec["mode"];
            props.onChange({ ...props.value, mode, code: props.value.code || KEY_ENTER });
            if (mode === "custom") setRecording(true);
          }}
        />
      </PanelSectionRow>
      {props.value.mode === "custom" && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setRecording(!recording)}>
            {recording
              ? "Listening for a KEYBOARD key... press it now (15s). On the Deck use Steam + X for the on-screen keyboard"
              : `${describeKey(props.value)}  (tap to re-record)`}
          </ButtonItem>
        </PanelSectionRow>
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
          <KeyPicker label="Press before typing" value={p.before_key}
            onChange={(k) => props.onChange({ ...p, before_key: k })} />
          <KeyPicker label="Press after typing" value={p.after_key}
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

function Panel() {
  const [settings, setLocal] = useState<Settings | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [capturing, setCapturing] = useState(false);
  const [overridesOpen, setOverridesOpen] = useState(false);
  const [monitor, setMonitor] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [upd, setUpd] = useState<UpdateInfo | null>(null);
  const [updMsg, setUpdMsg] = useState<string>("");
  const [updBusy, setUpdBusy] = useState(false);
  const appid = runningAppId();

  const refresh = async () => {
    setModels(await listModels());
    setStatus(await getStatus());
  };

  useEffect(() => {
    void getSettings().then(setLocal);
    void refresh();
    void checkUpdate().then(setUpd);
    const t = setInterval(async () => {
      setProgress(await getProgress());
      setStatus(await getStatus());
    }, 1500);
    return () => {
      clearInterval(t);
      monitorCb = null;
      captureCb = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    monitorCb = monitor === null ? null : setMonitor;
    if (monitor === null) return;
    return () => { monitorCb = null; };
  }, [monitor === null]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!settings) return <PanelSection title="Ubermoc Speech">Loading...</PanelSection>;

  const update = async (patch: Partial<Settings>) => {
    const next: Settings = { ...settings, ...patch };
    setLocal(next);
    current = next;
    await setSettings(next);
  };

  const setProfile = (id: string, p: Profile | null) => {
    const profiles = { ...settings.profiles };
    if (p) profiles[id] = p;
    else delete profiles[id];
    return update({ profiles });
  };

  const addOverride = (id: string, name: string) => {
    if (settings.profiles?.[id]) return;
    return setProfile(id, {
      name,
      before_key: settings.before_key,
      after_key: settings.after_key,
    });
  };

  const recordCombo = () => {
    setCapturing(true);
    const seen: Combo = { lower: 0, upper: 0 };
    const seenIds = new Set<number>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = () => {
      captureCb = null;
      setCapturing(false);
      const combo: Combo = { ...seen, ids: [...seenIds] };
      if (!comboEmpty(combo)) void update({ combo });
      else toaster.toast({ title: "Ubermoc Speech", body: "No button input detected - see 'Live input' below" });
    };
    // Give up after 10s of silence.
    timer = setTimeout(finish, 10000);
    captureCb = (mask, ids) => {
      if (!mask.lower && !mask.upper && ids.size === 0) return;
      seen.lower |= mask.lower;
      seen.upper |= mask.upper;
      ids.forEach((i) => seenIds.add(i));
      clearTimeout(timer);
      timer = setTimeout(finish, 800); // finish shortly after input settles
    };
  };

  const installed = models.filter((m) => m.installed);
  const apps = libraryApps().filter((a) => !settings.profiles?.[a.appid]);
  const profileEntries = Object.entries(settings.profiles ?? {});

  return (
    <>
      <PanelSection title="Global">
        <KeyPicker label="Press before typing" value={settings.before_key}
          onChange={(k) => update({ before_key: k })} />
        <KeyPicker label="Press after typing" value={settings.after_key}
          onChange={(k) => update({ after_key: k })} />
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={recordCombo} disabled={capturing}>
            {capturing
              ? "Listening for CONTROLLER buttons... hold them together now, then let go"
              : `Hold-to-talk buttons: ${describeCombo(settings.combo)}  (tap to change)`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => update({ combo: DEFAULT_COMBO })}>
            Reset hold-to-talk to R4 + R5
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Punctuation"
            description={
              settings.punctuation === "spoken"
                ? 'Only types punctuation you say out loud, e.g. "comma", "period", "question mark", "new line".'
                : "Whisper decides where punctuation goes. Saying \"period\" types the word."
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
            label="Live input"
            description={
              monitor ??
              `Listening feeds - Steam state: ${feeds.state ? "yes" : "NO"}, Steam buttons: ${feeds.messages ? "yes" : "NO"}, gamepad: ${feeds.pad ? "yes" : "NO"}, key output: ${feeds.keys ? "yes" : "NO"}`
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
            {apps.length > 0 && (
              <PanelSectionRow>
                <DropdownItem
                  label="Add from library"
                  rgOptions={apps.map((a) => ({ data: a.appid, label: a.name }))}
                  selectedOption={undefined}
                  strDefaultLabel="Choose a game or app..."
                  onChange={(o) => {
                    const a = apps.find((x) => x.appid === o.data);
                    if (a) void addOverride(a.appid, a.name);
                  }}
                />
              </PanelSectionRow>
            )}
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
              if (!r.ok) toaster.toast({ title: "Ubermoc Speech", body: (r as { error?: string }).error ?? "Failed" });
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
        <PanelSectionRow>
          <SliderField
            label="CPU threads"
            value={settings.threads}
            min={1}
            max={8}
            step={1}
            showValue
            onChange={(v) => update({ threads: v })}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Updates">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={updBusy}
            description={
              updMsg ||
              (upd
                ? upd.error ?? `Installed ${upd.installed}${upd.latest ? `, latest ${upd.latest}` : ""}`
                : "Checking...")
            }
            onClick={async () => {
              setUpdBusy(true);
              try {
                if (upd?.available) {
                  setUpdMsg("Downloading and installing...");
                  const r = await installUpdate();
                  setUpdMsg(
                    r.ok
                      ? "Updated. Steam's plugin loader is restarting - reopen the panel in a few seconds."
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
            {upd?.available ? `Install update (${upd.latest})` : "Check for updates"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Models">
        {installed.length > 0 && (
          <PanelSectionRow>
            <DropdownItem
              label="Active model"
              rgOptions={installed.map((m) => ({ data: m.name, label: m.name }))}
              selectedOption={settings.model}
              onChange={async (o) => {
                await update({ model: o.data as string });
                if (status?.running) {
                  await stopServer();
                  await startServer();
                }
              }}
            />
          </PanelSectionRow>
        )}
        {models.map((m) => (
          <PanelSectionRow key={m.name}>
            {progress[m.name] !== undefined ? (
              <ProgressBarWithInfo
                label={`Downloading ${m.name}`}
                nProgress={progress[m.name] * 100}
                sOperationText={`${Math.round(progress[m.name] * 100)}%`}
              />
            ) : m.installed ? (
              <ButtonItem
                layout="below"
                description={`${m.size_mb} MB - saved permanently`}
                disabled={m.name === settings.model}
                onClick={async () => {
                  if (confirmDelete !== m.name) {
                    setConfirmDelete(m.name);
                    setTimeout(() => setConfirmDelete((c) => (c === m.name ? null : c)), 4000);
                    return;
                  }
                  setConfirmDelete(null);
                  await deleteModel(m.name);
                  await refresh();
                }}
              >
                {confirmDelete === m.name ? `Tap again to delete ${m.name}` : `${m.name} installed (delete)`}
              </ButtonItem>
            ) : (
              <ButtonItem
                layout="below"
                description={`${m.size_mb} MB`}
                onClick={async () => {
                  setProgress({ ...progress, [m.name]: 0 });
                  const r = await downloadModel(m.name);
                  if (!r.ok) toaster.toast({ title: "Download failed", body: r.error ?? "" });
                  await refresh();
                }}
              >
                Download {m.name}
              </ButtonItem>
            )}
          </PanelSectionRow>
        ))}
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
  const input = (typeof SteamClient !== "undefined" && SteamClient?.Input) || {};
  const names: string[] = [];
  // eslint-disable-next-line guard-for-in
  for (const k in input) if (/Controller|Register|Key/i.test(k)) names.push(k);
  feeds.names = names.join(",");
  void logBackend(`SteamClient.Input functions: ${feeds.names || "(none)"}`);

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
  feeds.state = typeof input.RegisterForControllerStateChanges === "function" && regState !== null;
  feeds.messages = typeof input.RegisterForControllerInputMessages === "function" && regMsg !== null;
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
