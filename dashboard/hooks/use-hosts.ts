"use client";

import { useQuery } from "@tanstack/react-query";
import { getHost, getHostPorts, listHosts } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { ListHostsParams } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/** GET /api/hosts -- the full host inventory (used by Overview + Hosts page). */
export function useHosts(params: ListHostsParams = {}) {
  return useQuery({
    queryKey: queryKeys.hosts.list(params),
    queryFn: ({ signal }) => listHosts(params, signal),
    staleTime: STALE_TIME_MS,
  });
}

/** GET /api/hosts/{host_id} -- a single host's detail record. */
export function useHost(hostId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.hosts.detail(hostId ?? ""),
    queryFn: ({ signal }) => getHost(hostId as string, signal),
    enabled: Boolean(hostId),
    staleTime: STALE_TIME_MS,
  });
}

/** GET /api/hosts/{host_id}/ports -- current physical bindings for one host. */
export function useHostPorts(hostId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.hosts.ports(hostId ?? ""),
    queryFn: ({ signal }) => getHostPorts(hostId as string, signal),
    enabled: Boolean(hostId),
    staleTime: STALE_TIME_MS,
  });
}

/** GET /api/hosts/{host_id}/diagnostics */
export function useHostDiagnostics(hostId: string | undefined) {
  return useQuery({
    queryKey: ["hosts", hostId, "diagnostics"],
    queryFn: ({ signal }) => import("@/lib/api/resources").then((m) => m.getHostDiagnostics(hostId as string, signal)),
    enabled: Boolean(hostId),
    staleTime: STALE_TIME_MS,
  });
}
