"use client";

import { useQuery } from "@tanstack/react-query";
import { listPorts } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { ListPortsParams } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/** GET /api/ports -- the global cross-host port table. */
export function usePorts(params: ListPortsParams = {}) {
  return useQuery({
    queryKey: queryKeys.ports.list(params),
    queryFn: ({ signal }) => listPorts(params, signal),
    staleTime: STALE_TIME_MS,
    placeholderData: (previousData) => previousData,
  });
}
