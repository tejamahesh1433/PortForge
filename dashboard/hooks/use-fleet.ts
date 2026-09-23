"use client";

import { useQuery } from "@tanstack/react-query";
import { listFleet, getFleetHost } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { ListFleetParams } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/** GET /api/fleet -- fleet intelligence view with filtering. */
export function useFleet(params: ListFleetParams = {}) {
  return useQuery({
    queryKey: queryKeys.fleet.list(params),
    queryFn: ({ signal }) => listFleet(params, signal),
    staleTime: STALE_TIME_MS,
  });
}

/** GET /api/fleet/{host_id} -- single fleet host with upgrade state. */
export function useFleetHost(hostId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.fleet.detail(hostId ?? ""),
    queryFn: ({ signal }) => getFleetHost(hostId as string, signal),
    enabled: Boolean(hostId),
    staleTime: STALE_TIME_MS,
  });
}
