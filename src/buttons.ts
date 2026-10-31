// Controller combo matching.
//
// A combo can be satisfied by either of two independent Steam input feeds, and
// "Record combo" captures both at once, so recording works even if one feed
// does not report a given button on a particular SteamOS/Steam build:
//   * raw state bitmasks  (SteamClient.Input.RegisterForControllerStateChanges)
//   * button ids          (SteamClient.Input.RegisterForControllerInputMessages)
// The default (R4 + R5) uses masks written from memory and is UNVERIFIED on
// hardware: if it does nothing, record the combo from the panel instead.
export const LOWER = { R5: 0x10000, L5: 0x8000 } as const;
export const UPPER = { R4: 0x400, L4: 0x200 } as const;

export interface Combo {
  lower: number;
  upper: number;
  ids?: number[];
}

export const DEFAULT_COMBO: Combo = { lower: LOWER.R5, upper: UPPER.R4, ids: [] };

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
  // Each feed is judged on its own: the combo is held if ANY feed reports
  // all of the buttons it recorded (ids >= 1000 come from the Gamepad API).
  const ids = combo.ids ?? [];
  const steam = ids.filter((i) => i < 1000);
  const pad = ids.filter((i) => i >= 1000);
  const byIds =
    (steam.length > 0 && steam.every((i) => heldIds.has(i))) ||
    (pad.length > 0 && pad.every((i) => heldIds.has(i)));
  return !!(byMask || byIds);
}

const NAMES: Array<[string, "lower" | "upper", number]> = [
  ["R5", "lower", LOWER.R5], ["L5", "lower", LOWER.L5],
  ["R4", "upper", UPPER.R4], ["L4", "upper", UPPER.L4],
];

export function describeCombo(c: Combo): string {
  if (comboEmpty(c)) return "Not set";
  const parts: string[] = [];
  let lower = c.lower;
  let upper = c.upper;
  for (const [name, which, bit] of NAMES) {
    const have = which === "lower" ? lower : upper;
    if ((have & bit) === bit) {
      parts.push(name);
      if (which === "lower") lower &= ~bit; else upper &= ~bit;
    }
  }
  if (lower || upper) parts.push(`buttons ${upper.toString(16)}:${lower.toString(16)}`);
  if (c.ids?.length) parts.push(`Steam ids ${c.ids.join("+")}`);
  return parts.join(" + ");
}
