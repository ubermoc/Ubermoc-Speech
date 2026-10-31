// Controller combo matching.
//
// A combo can be satisfied by any of three independent input feeds. "Record"
// captures from all of them, so recording works even if one feed does not
// report a particular button on a given Steam build:
//   * Steam button ids   - SteamClient.Input.RegisterForControllerInputMessages
//                          (ControllerInputGamepadButton enum, ids < 1000)
//   * raw state bitmasks - SteamClient.Input.RegisterForControllerStateChanges
//   * browser Gamepad API - ids offset by 1000
export interface Combo {
  lower: number;
  upper: number;
  ids?: number[];
}

// Right back buttons (GAMEPAD_BUTTON_RBACK = 33, GAMEPAD_BUTTON_RBACK_UPPER = 45).
export const DEFAULT_COMBO: Combo = { lower: 0, upper: 0, ids: [33, 45] };

const STEAM_NAMES: Record<number, string> = {
  0: "A", 1: "B", 2: "X", 3: "Y",
  4: "D-pad Up", 5: "D-pad Right", 6: "D-pad Down", 7: "D-pad Left",
  8: "Menu", 9: "View",
  25: "L3", 41: "R3",
  28: "L2", 29: "R2", 30: "L1", 31: "R1",
  32: "Left back", 33: "Right back", 44: "Left back upper", 45: "Right back upper",
  34: "Steam", 35: "Select", 36: "Start",
  37: "Left pad click", 38: "Left pad touch", 39: "Right pad click", 40: "Right pad touch",
};
const PAD_NAMES: Record<number, string> = {
  0: "A", 1: "B", 2: "X", 3: "Y", 4: "L1", 5: "R1", 6: "L2", 7: "R2",
  8: "View", 9: "Menu", 10: "L3", 11: "R3",
  12: "D-pad Up", 13: "D-pad Down", 14: "D-pad Left", 15: "D-pad Right", 16: "Steam",
};

// Only these Steam ids can be part of a combo (no analog sticks/triggers/pads).
export function recordableSteamId(id: number): boolean {
  return id in STEAM_NAMES;
}

export function idName(id: number): string {
  if (id >= 1000) return PAD_NAMES[id - 1000] ?? `pad ${id - 1000}`;
  return STEAM_NAMES[id] ?? `button ${id}`;
}

export function comboEmpty(c: Combo): boolean {
  return !c.lower && !c.upper && !(c.ids && c.ids.length);
}

export function comboHeld(
  state: { lower: number; upper: number },
  heldIds: Set<number>,
  combo: Combo
): boolean {
  if (comboEmpty(combo)) return false;
  const byMask =
    (combo.lower || combo.upper) &&
    (state.lower & combo.lower) === combo.lower &&
    (state.upper & combo.upper) === combo.upper;
  // Each feed is judged on its own: held if ANY feed reports all its buttons.
  const ids = combo.ids ?? [];
  const steam = ids.filter((i) => i < 1000);
  const pad = ids.filter((i) => i >= 1000);
  const byIds =
    (steam.length > 0 && steam.every((i) => heldIds.has(i))) ||
    (pad.length > 0 && pad.every((i) => heldIds.has(i)));
  return !!(byMask || byIds);
}

export function describeCombo(c: Combo): string {
  if (comboEmpty(c)) return "Not set";
  const ids = c.ids ?? [];
  const steam = ids.filter((i) => i < 1000);
  if (steam.length) return steam.map(idName).join(" + ");
  const pad = ids.filter((i) => i >= 1000);
  if (pad.length) return pad.map(idName).join(" + ");
  return `buttons ${c.upper.toString(16)}:${c.lower.toString(16)}`;
}

export function describePressed(ids: Set<number>): string {
  const names = [...ids].filter((i) => i >= 1000 || recordableSteamId(i)).map(idName);
  return names.length ? names.join(" + ") : "(nothing pressed)";
}
