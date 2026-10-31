// Map KeyboardEvent.code -> USB HID usage id, as used by
// SteamClient.Input.ControllerKeyboardSetKeyState.
const HID: Record<string, number> = {
  Enter: 40, Escape: 41, Backspace: 42, Tab: 43, Space: 44,
  Minus: 45, Equal: 46, BracketLeft: 47, BracketRight: 48, Backslash: 49,
  Semicolon: 51, Quote: 52, Backquote: 53, Comma: 54, Period: 55, Slash: 56,
  F1: 58, F2: 59, F3: 60, F4: 61, F5: 62, F6: 63, F7: 64, F8: 65, F9: 66, F10: 67, F11: 68, F12: 69,
  Insert: 73, Home: 74, PageUp: 75, Delete: 76, End: 77, PageDown: 78,
  ArrowRight: 79, ArrowLeft: 80, ArrowDown: 81, ArrowUp: 82,
  ControlLeft: 224, ShiftLeft: 225, AltLeft: 226, MetaLeft: 227,
  ControlRight: 228, ShiftRight: 229, AltRight: 230, MetaRight: 231,
};
for (let i = 0; i < 26; i++) HID[`Key${String.fromCharCode(65 + i)}`] = 4 + i;
for (let i = 1; i <= 9; i++) HID[`Digit${i}`] = 29 + i;
HID["Digit0"] = 39;

export const MOD_CODES = new Set([224, 225, 226, 227, 228, 229, 230, 231]);
export const KEY_V = 25;
export const KEY_CTRL = 224;
export const KEY_ENTER = 40;

export function hidFromEvent(e: KeyboardEvent): number | undefined {
  return HID[e.code];
}

export function keyName(code: number): string {
  for (const [name, c] of Object.entries(HID)) if (c === code) return name;
  return `HID ${code}`;
}

export interface KeySpec {
  mode: "none" | "enter" | "custom";
  code: number; // main key (HID)
  mods?: number[]; // modifier HID codes held while pressing
}

export function describeKey(k: KeySpec): string {
  if (k.mode === "none") return "None";
  if (k.mode === "enter") return "Enter";
  return [...(k.mods ?? []).map(keyName), keyName(k.code)].join(" + ");
}
