// Copyright (c) 2026 ubermoc. All rights reserved.
// Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
// train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
// except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c

// The Applications dropdown in the Applications overrides group. Kept as a plain function so the
// order and labels can be checked without the Decky runtime.

export interface LibApp { appid: string; name: string }
export interface Option { data: string; label: string }

// One dropdown: Steam games first, then apps added to Steam as non-Steam shortcuts, alphabetical
// inside each category, every entry labelled with its category. A separator row labels the
// category. Applications that are hidden never appear here.
export function appOptions(steam: LibApp[], other: LibApp[], hidden: string[]): Option[] {
  const byName = (a: LibApp, b: LibApp) => a.name.localeCompare(b.name);
  const visible = (list: LibApp[]) => list.filter((a) => !hidden.includes(a.appid)).slice().sort(byName);
  const steamList = visible(steam);
  const otherList = visible(other);
  const out: Option[] = [];
  if (steamList.length) {
    out.push({ data: "category", label: "Steam games" });
    for (const a of steamList) out.push({ data: a.appid, label: `Steam - ${a.name}` });
  }
  if (otherList.length) {
    out.push({ data: "category", label: "Non-Steam apps" });
    for (const a of otherList) out.push({ data: a.appid, label: `Non-Steam - ${a.name}` });
  }
  return out;
}

// The Hidden applications dropdown: every hidden application, by name, alphabetical.
export function hiddenOptions(names: Record<string, string>, hidden: string[]): Option[] {
  return hidden
    .slice()
    .sort((a, b) => (names[a] ?? a).localeCompare(names[b] ?? b))
    .map((id) => ({ data: id, label: names[id] ?? `App ${id}` }));
}
