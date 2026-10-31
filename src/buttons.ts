// Raw Steam Input button bitmasks as reported by
// SteamClient.Input.RegisterForControllerStateChanges.
// NOTE: values come from memory and are UNVERIFIED on hardware. The panel's
// "Record combo" feature stores whatever masks it actually observes, so a wrong
// constant here only affects the factory default (R4 + R5).
export const LOWER = {
  R5: 0x10000,
  L5: 0x8000,
} as const;

export const UPPER = {
  R4: 0x400,
  L4: 0x200,
} as const;

export interface Combo {
  lower: number;
  upper: number;
}

export const DEFAULT_COMBO: Combo = { lower: LOWER.R5, upper: UPPER.R4 };

export function comboHeld(state: Combo, combo: Combo): boolean {
  if (combo.lower === 0 && combo.upper === 0) return false;
  return (
    (state.lower & combo.lower) === combo.lower &&
    (state.upper & combo.upper) === combo.upper
  );
}
