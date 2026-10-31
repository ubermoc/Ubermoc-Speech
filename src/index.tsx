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
import { useEffect, useState } from "react";
import { FaMicrophone } from "react-icons/fa";
import { Combo, DEFAULT_COMBO, comboHeld } from "./buttons";
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
  combo: Combo;
  before_key: KeySpec;
  after_key: KeySpec;
  profiles: Record<string, Partial<Settings>>;
}
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
  return { ...s, ...(s.profiles?.[appid] ?? {}) };
}

// ---------- key output ----------
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function tapKeys(mods: number[], code: number) {
  const input = SteamClient.Input;
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

// While set, the controller handler reports raw pressed masks to this callback
// instead of acting on the combo (used by "Record combo").
let captureCb: ((c: Combo) => void) | null = null;

function onControllerState(states: any[]) { // eslint-disable-line @typescript-eslint/no-explicit-any
  if (!current) return;
  const st = states?.[0];
  if (!st) return;
  const pressed: Combo = {
    lower: Number(st.ulButtons ?? 0) >>> 0,
    upper: Number(st.ulUpperButtons ?? 0) >>> 0,
  };
  if (captureCb) {
    captureCb(pressed);
    return;
  }
  const combo = effective(current, runningAppId()).combo ?? DEFAULT_COMBO;
  const down = comboHeld(pressed, combo);
  if (down && !held) {
    held = true;
    void onPress();
  } else if (!down && held) {
    held = false;
    void onRelease();
  }
}

// ---------- UI ----------
function describeCombo(c: Combo): string {
  if (!c.lower && !c.upper) return "Not set";
  return `0x${c.upper.toString(16)}:${c.lower.toString(16)}`;
}

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
      const code = hidFromEvent(e);
      if (code === undefined) return;
      if (MOD_CODES.has(code)) {
        mods.add(code);
        return;
      }
      props.onChange({ mode: "custom", code, mods: [...mods] });
      setRecording(false);
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [recording]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <PanelSectionRow>
        <DropdownItem
          label={props.label}
          rgOptions={[
            { data: "none", label: "None" },
            { data: "enter", label: "Enter" },
            { data: "custom", label: "Custom" },
          ]}
          selectedOption={props.value.mode}
          onChange={(o) => {
            const mode = o.data as KeySpec["mode"];
            props.onChange({ ...props.value, mode, code: props.value.code || KEY_ENTER });
          }}
        />
      </PanelSectionRow>
      {props.value.mode === "custom" && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setRecording(!recording)}>
            {recording ? "Press the key now..." : `Key: ${describeKey(props.value)} (tap to record)`}
          </ButtonItem>
        </PanelSectionRow>
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
  const [perGame, setPerGame] = useState(false);
  const appid = runningAppId();

  const refresh = async () => {
    setModels(await listModels());
    setStatus(await getStatus());
  };

  useEffect(() => {
    void getSettings().then((s) => {
      setLocal(s);
      setPerGame(!!appid && !!s.profiles?.[appid]);
    });
    void refresh();
    const t = setInterval(async () => {
      setProgress(await getProgress());
      setStatus(await getStatus());
    }, 1500);
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!settings) return <PanelSection title="Ubermoc Speech">Loading...</PanelSection>;

  const view = perGame && appid ? effective(settings, appid) : settings;

  const update = async (patch: Partial<Settings>) => {
    const next: Settings =
      perGame && appid
        ? { ...settings, profiles: { ...settings.profiles, [appid]: { ...settings.profiles?.[appid], ...patch } } }
        : { ...settings, ...patch };
    setLocal(next);
    current = next;
    await setSettings(next);
  };

  const toggleProfile = async (on: boolean) => {
    setPerGame(on);
    if (!appid) return;
    const profiles = { ...settings.profiles };
    if (on) profiles[appid] = {};
    else delete profiles[appid];
    const next = { ...settings, profiles };
    setLocal(next);
    current = next;
    await setSettings(next);
  };

  const recordCombo = () => {
    setCapturing(true);
    let best: Combo = { lower: 0, upper: 0 };
    let timer: ReturnType<typeof setTimeout> | undefined;
    captureCb = (c) => {
      if (c.lower === 0 && c.upper === 0) return;
      // keep the largest set seen; finish shortly after input settles
      best = { lower: best.lower | c.lower, upper: best.upper | c.upper };
      clearTimeout(timer);
      timer = setTimeout(() => {
        captureCb = null;
        setCapturing(false);
        void update({ combo: best });
      }, 700);
    };
  };

  const installed = models.filter((m) => m.installed);

  return (
    <>
      <PanelSection title="Server">
        <PanelSectionRow>
          <ToggleField
            label={status?.running ? "Whisper server running" : "Whisper server stopped"}
            description={status && !status.binary ? "whisper-server binary missing" : undefined}
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
            label="Start with Steam"
            checked={settings.autostart}
            onChange={(v) => update({ autostart: v })}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Models">
        {models.map((m) => (
          <PanelSectionRow key={m.name}>
            {progress[m.name] !== undefined ? (
              <ProgressBarWithInfo
                label={`Downloading ${m.name}`}
                nProgress={progress[m.name] * 100}
                sOperationText={`${Math.round(progress[m.name] * 100)}%`}
              />
            ) : (
              <ButtonItem
                layout="below"
                description={`${m.size_mb} MB`}
                onClick={async () => {
                  if (m.installed) await deleteModel(m.name);
                  else {
                    setProgress({ ...progress, [m.name]: 0 });
                    const r = await downloadModel(m.name);
                    if (!r.ok) toaster.toast({ title: "Download failed", body: r.error ?? "" });
                  }
                  await refresh();
                }}
              >
                {m.installed ? `Delete ${m.name}` : `Download ${m.name}`}
              </ButtonItem>
            )}
          </PanelSectionRow>
        ))}
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
      </PanelSection>

      <PanelSection title="Hold to talk">
        {appid && (
          <PanelSectionRow>
            <ToggleField
              label="Use per-game profile"
              description={`Overrides for app ${appid}`}
              checked={perGame}
              onChange={toggleProfile}
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={recordCombo} disabled={capturing}>
            {capturing
              ? "Hold your buttons, then release..."
              : `Combo: ${describeCombo(view.combo)} (tap to record)`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => update({ combo: DEFAULT_COMBO })}>
            Reset combo to R4 + R5
          </ButtonItem>
        </PanelSectionRow>
        <KeyPicker label="Key before text" value={view.before_key} onChange={(k) => update({ before_key: k })} />
        <KeyPicker label="Key after text" value={view.after_key} onChange={(k) => update({ after_key: k })} />
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
    </>
  );
}

export default definePlugin(() => {
  void getSettings().then((s) => {
    current = s;
  });
  const reg = SteamClient.Input.RegisterForControllerStateChanges(onControllerState);

  return {
    name: "Ubermoc Speech",
    titleView: <div>Ubermoc Speech</div>,
    content: <Panel />,
    icon: <FaMicrophone />,
    onDismount() {
      reg?.unregister?.();
      captureCb = null;
    },
  };
});
