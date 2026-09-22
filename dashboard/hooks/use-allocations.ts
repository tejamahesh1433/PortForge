"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getAllocation, listAllocations, releaseAllocation } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { ListAllocationsParams } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/** GET /api/allocations */
export function useAllocations(params: ListAllocationsParams = {}) {
  return useQuery({
    queryKey: queryKeys.allocations.list(params),
    queryFn: ({ signal }) => listAllocations(params, signal),
    staleTime: STALE_TIME_MS,
  });
}

/** GET /api/allocations/{allocation_id} */
export function useAllocation(allocationId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.allocations.detail(allocationId ?? ""),
    queryFn: ({ signal }) => getAllocation(allocationId as string, signal),
    enabled: Boolean(allocationId),
    staleTime: STALE_TIME_MS,
  });
}

/**
 * DELETE /api/allocations/{allocation_id} -- the real allocation-release
 * contract (Phase 8A: releases every reservation in the bundle as one
 * unit), never individual reservation deletion. Idempotent server-side, so
 * a double-click/repeat call is safe -- the mutation itself isn't
 * additionally debounced, matching useDeleteDashboardReservation's own
 * posture.
 */
export function useReleaseAllocation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (allocationId: string) => releaseAllocation(allocationId),
    onSuccess: (_data, allocationId) => {
      queryClient.invalidateQueries({ queryKey: ["allocations"] });
      queryClient.invalidateQueries({ queryKey: ["reservations"] });
      queryClient.invalidateQueries({ queryKey: ["ports"] });
      queryClient.invalidateQueries({ queryKey: ["conflicts"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["activity"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.allocations.detail(allocationId) });
    },
  });
}
