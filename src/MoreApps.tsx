// Copyright (c) 2026 ubermoc. All rights reserved. See LICENSE.
import { ButtonItem, Navigation, PanelSection, PanelSectionRow } from "@decky/ui";
import { useState } from "react";

const APPS: { name: string; what: string; url: string }[] = [
  { name: "Uber Watch", what: "Pause, unload and reload your local model and its watchdog.", url: "https://github.com/ubermoc/Uber-Watch" },
  { name: "Uber Watch for the desktop", what: "The same watchdog with a tray icon, for your Linux desktop.", url: "https://github.com/ubermoc/Uber-Watch-Desktop" },
  { name: "Ubermoc Speech", what: "Hold a button, speak, and your words are typed into any app.", url: "https://github.com/ubermoc/Ubermoc-Speech" },
  { name: "Ubermoc Warcraft Mod Updater", what: "Updates the Ubermoc addons for World of Warcraft.", url: "https://github.com/ubermoc/Ubermoc-Warcraft-Mod-Updater" },
];

// A collapsed list of the other Uber apps for game mode; `self` is this plugin's own page and is left out.
export function MoreApps(props: { self: string }) {
  const [open, setOpen] = useState(false);
  return (
    <PanelSection title="More from Ubermoc">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setOpen(!open)}>
          {open ? "Hide the list" : "Downloads: more Uber apps for game mode"}
        </ButtonItem>
      </PanelSectionRow>
      {open &&
        APPS.filter((a) => a.url !== props.self).map((a) => (
          <PanelSectionRow {...({ key: a.url } as object)}>
            <ButtonItem layout="below" description={a.what} onClick={() => Navigation.NavigateToExternalWeb(a.url)}>
              {a.name}
            </ButtonItem>
          </PanelSectionRow>
        ))}
    </PanelSection>
  );
}
