"use client";

import { useQuery } from "@tanstack/react-query";
import { listConflicts } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { ListConflictsParams } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/**
 * GET /api/conflicts -- host-scoped by design (see
 * backend/app/services/conflict_service.py): a conflict is always a
 * same-host reservation-vs-actual mismatch. Passing no `host_id` returns
 * conflicts across every host Central knows about; this never fabricates
 * a cross-host conflict that Central itself didn't report.
 */
export function useConflicts(params: ListConflictsParams = {}) {
  return useQuery({
    queryKey: queryKeys.conflicts.list(params),
    queryFn: ({ signal }) => listConflicts(params, signal),
    staleTime: STALE_TIME_MS,
  });
}
