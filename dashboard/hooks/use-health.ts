"use client";

import { useQuery } from "@tanstack/react-query";
import { getHealth } from "@/lib/api/resources";
import { REFETCH_INTERVAL_MS, STALE_TIME_MS } from "@/lib/query-config";
import { queryKeys } from "./query-keys";

/** Central's own health/connectivity status -- drives the TopBar's
 * connection indicator, so it polls in the background even when no page
 * explicitly needs it (mounted from AppShell).
 */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health(),
    queryFn: ({ signal }) => getHealth(signal),
    staleTime: STALE_TIME_MS,
    refetchInterval: REFETCH_INTERVAL_MS,
    retry: 1,
  });
}

/** GET /api/diagnostics -- Global system diagnostics */
export function useGlobalDiagnostics() {
  return useQuery({
    queryKey: ["health", "diagnostics"],
    queryFn: ({ signal }) => import("@/lib/api/resources").then((m) => m.getGlobalDiagnostics(signal)),
    staleTime: STALE_TIME_MS,
  });
}
