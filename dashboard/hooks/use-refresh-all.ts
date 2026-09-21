"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

/**
 * Backs the TopBar's manual refresh control: invalidates every active
 * PortForge query (triggering an immediate refetch for whatever's
 * currently mounted) rather than a hard page reload, and exposes a short
 * `isRefreshing` window so the UI can show a brief spin/feedback state
 * even when the underlying refetch resolves near-instantly.
 */
export function useRefreshAll() {
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const refreshAll = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await queryClient.invalidateQueries();
    } finally {
      setIsRefreshing(false);
    }
  }, [queryClient]);

  return { refreshAll, isRefreshing };
}
