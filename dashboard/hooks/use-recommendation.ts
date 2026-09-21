"use client";

import { useQuery } from "@tanstack/react-query";
import { getRecommendation } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { GetRecommendationParams } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/**
 * GET /api/recommendations -- only enabled once the caller has picked a
 * concrete host + service type (see app/recommendations/page.tsx); there
 * is no "list all recommendations" endpoint to poll speculatively.
 */
export function useRecommendation(params: GetRecommendationParams | undefined) {
  return useQuery({
    queryKey: queryKeys.recommendations.get(params ?? { host_id: "", service_type: "" }),
    queryFn: ({ signal }) => getRecommendation(params as GetRecommendationParams, signal),
    enabled: Boolean(params?.host_id && params?.service_type),
    staleTime: STALE_TIME_MS,
    retry: false,
  });
}
