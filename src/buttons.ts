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
  const byIds = !!combo.ids?.length && combo.ids.every((i) => heldIds.has(i));
  return !!(byMask || byIds);
}

export function describeCombo(c: Combo): string {
  if (comboEmpty(c)) return "Not set";
  const parts: string[] = [];
  if (c.lower || c.upper) parts.push(`mask ${c.upper.toString(16)}:${c.lower.toString(16)}`);
  if (c.ids?.length) parts.push(`ids ${c.ids.join("+")}`);
  return parts.join(" / ");
}
