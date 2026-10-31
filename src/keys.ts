// USB HID usage ids, as used by SteamClient.Input.ControllerKeyboardSetKeyState
// (Steam's EHIDKeyboardKey enum is the same numbering: A = 4 ... Enter = 40).
export const KEY_CTRL = 224;
export const KEY_SHIFT = 225;
export const KEY_ALT = 226;
export const KEY_META = 227;
export const KEY_V = 25;
export const KEY_ENTER = 40;

export interface KeyOption { code: number; label: string }

export const KEY_OPTIONS: KeyOption[] = [
  { code: 40, label: "Enter" },
  { code: 43, label: "Tab" },
  { code: 44, label: "Space" },
  { code: 41, label: "Escape" },
  { code: 42, label: "Backspace" },
  { code: 76, label: "Delete" },
  ...Array.from({ length: 26 }, (_, i) => ({ code: 4 + i, label: String.fromCharCode(65 + i) })),
  ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => ({ code: 29 + n, label: String(n) })),
  { code: 39, label: "0" },
  ...Array.from({ length: 12 }, (_, i) => ({ code: 58 + i, label: `F${i + 1}` })),
  { code: 82, label: "Up arrow" },
  { code: 81, label: "Down arrow" },
  { code: 80, label: "Left arrow" },
  { code: 79, label: "Right arrow" },
  { code: 74, label: "Home" },
  { code: 77, label: "End" },
  { code: 75, label: "Page Up" },
  { code: 78, label: "Page Down" },
  { code: 73, label: "Insert" },
  { code: 56, label: "/ (slash)" },
  { code: 55, label: ". (period)" },
  { code: 54, label: ", (comma)" },
  { code: 51, label: "; (semicolon)" },
  { code: 52, label: "' (apostrophe)" },
  { code: 45, label: "- (minus)" },
  { code: 46, label: "= (equals)" },
  { code: 47, label: "[" },
  { code: 48, label: "]" },
  { code: 49, label: "\\ (backslash)" },
  { code: 53, label: "` (backtick)" },
];

export const MOD_OPTIONS: { label: string; mods: number[] }[] = [
  { label: "No modifier", mods: [] },
  { label: "Ctrl", mods: [KEY_CTRL] },
  { label: "Shift", mods: [KEY_SHIFT] },
  { label: "Alt", mods: [KEY_ALT] },
  { label: "Super (Windows key)", mods: [KEY_META] },
  { label: "Ctrl + Shift", mods: [KEY_CTRL, KEY_SHIFT] },
  { label: "Ctrl + Alt", mods: [KEY_CTRL, KEY_ALT] },
  { label: "Alt + Shift", mods: [KEY_ALT, KEY_SHIFT] },
  { label: "Ctrl + Alt + Shift", mods: [KEY_CTRL, KEY_ALT, KEY_SHIFT] },
];

const MOD_NAMES: Record<number, string> = {
  [KEY_CTRL]: "Ctrl", [KEY_SHIFT]: "Shift", [KEY_ALT]: "Alt", [KEY_META]: "Super",
  228: "Ctrl", 229: "Shift", 230: "Alt", 231: "Super",
};

export function modsIndex(mods: number[] | undefined): number {
  const norm = (mods ?? [])
    .map((m) => (m >= 228 ? m - 4 : m))
    .sort((a, b) => a - b)
    .join(",");
  const i = MOD_OPTIONS.findIndex((o) => [...o.mods].sort((a, b) => a - b).join(",") === norm);
  return i < 0 ? 0 : i;
}

export function keyLabel(code: number): string {
  return KEY_OPTIONS.find((k) => k.code === code)?.label ?? `key ${code}`;
}

const PUNCT: Record<string, number> = {
  " ": 44, "-": 45, "=": 46, "[": 47, "]": 48, "\\": 49, ";": 51, "'": 52,
  "`": 53, ",": 54, ".": 55, "/": 56,
};
const NAMED: Record<string, number> = {
  Enter: 40, Escape: 41, Backspace: 42, Tab: 43, Delete: 76, Insert: 73,
  Home: 74, End: 77, PageUp: 75, PageDown: 78,
  ArrowRight: 79, ArrowLeft: 80, ArrowDown: 81, ArrowUp: 82,
};

// Works with physical keyboards (e.code) and with Steam's on-screen keyboard,
// which may leave e.code empty (falls back to e.key).
export function hidFromEvent(e: KeyboardEvent): number | undefined {
  const c = e.code;
  if (c) {
    if (/^Key[A-Z]$/.test(c)) return 4 + c.charCodeAt(3) - 65;
    if (/^Digit[0-9]$/.test(c)) return c === "Digit0" ? 39 : 29 + Number(c[5]);
    if (/^F([1-9]|1[0-2])$/.test(c)) return 58 + Number(c.slice(1)) - 1;
    if (NAMED[c] !== undefined) return NAMED[c];
    const m: Record<string, number> = {
      Space: 44, Minus: 45, Equal: 46, BracketLeft: 47, BracketRight: 48, Backslash: 49,
      Semicolon: 51, Quote: 52, Backquote: 53, Comma: 54, Period: 55, Slash: 56,
    };
    if (m[c] !== undefined) return m[c];
  }
  const k = e.key;
  if (!k) return undefined;
  if (NAMED[k] !== undefined) return NAMED[k];
  if (/^F([1-9]|1[0-2])$/.test(k)) return 58 + Number(k.slice(1)) - 1;
  if (k.length === 1) {
    const up = k.toUpperCase();
    if (up >= "A" && up <= "Z") return 4 + up.charCodeAt(0) - 65;
    if (k >= "0" && k <= "9") return k === "0" ? 39 : 29 + Number(k);
    if (PUNCT[k] !== undefined) return PUNCT[k];
  }
  return undefined;
}

export function modsFromEvent(e: KeyboardEvent): number[] {
  const mods: number[] = [];
  if (e.ctrlKey) mods.push(KEY_CTRL);
  if (e.altKey) mods.push(KEY_ALT);
  if (e.shiftKey) mods.push(KEY_SHIFT);
  if (e.metaKey) mods.push(KEY_META);
  return mods;
}

export interface KeySpec {
  mode: "none" | "enter" | "custom";
  code: number; // main key (HID)
  mods?: number[]; // modifiers held while pressing
}

export function describeKey(k: KeySpec): string {
  if (k.mode === "none") return "None";
  if (k.mode === "enter") return "Enter";
  const mods = (k.mods ?? []).map((m) => MOD_NAMES[m] ?? `mod ${m}`);
  return [...mods, keyLabel(k.code)].join(" + ");
}
