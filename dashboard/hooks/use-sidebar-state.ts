"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "portforge:sidebar-collapsed";

/** Persists the desktop sidebar's collapsed/expanded state across page
 * loads via localStorage. Reads are guarded for SSR (localStorage is
 * unavailable during server render) and for a blocked/unavailable store
 * (private browsing, disabled site data) -- falls back to expanded
 * rather than throwing either way.
 */
export function useSidebarState() {
  const [collapsed, setCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // Deliberately deferred to a mount effect rather than a useState lazy
  // initializer: localStorage is only readable client-side, and applying
  // it during the initializer would make the client's first render
  // disagree with the server-rendered HTML (a hydration mismatch).
  // Reading it here, one render after hydration completes, trades a
  // single-frame "expanded" flash for correctness; `hydrated`
  // additionally suppresses the width transition for that one frame (see
  // sidebar.tsx) so the flash isn't an animated jump.
  /* eslint-disable react-hooks/set-state-in-effect -- see comment above */
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === "true") setCollapsed(true);
    } catch {
      // localStorage unavailable -- keep the expanded default.
    }
    setHydrated(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // Best-effort persistence only.
      }
      return next;
    });
  };

  return { collapsed, toggle, hydrated };
}
