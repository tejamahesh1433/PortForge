import type {
  GetRecommendationParams,
  ListConflictsParams,
  ListHostsParams,
  ListPortsParams,
  ListReservationsParams,
} from "@/lib/types/api";

/**
 * Centralized TanStack Query key factory. Every hook in hooks/*.ts builds
 * its key from here rather than inlining ad hoc arrays, so cache
 * invalidation (see hooks/use-refresh-all.ts) can target a whole resource
 * family reliably.
 */
export const queryKeys = {
  health: () => ["health"] as const,
  hosts: {
    list: (params: ListHostsParams = {}) => ["hosts", "list", params] as const,
    detail: (hostId: string) => ["hosts", "detail", hostId] as const,
    ports: (hostId: string) => ["hosts", "ports", hostId] as const,
  },
  ports: {
    list: (params: ListPortsParams = {}) => ["ports", "list", params] as const,
  },
  projects: {
    list: () => ["projects", "list"] as const,
    detail: (projectName: string) => ["projects", "detail", projectName] as const,
  },
  reservations: {
    list: (params: ListReservationsParams = {}) => ["reservations", "list", params] as const,
  },
  conflicts: {
    list: (params: ListConflictsParams = {}) => ["conflicts", "list", params] as const,
  },
  recommendations: {
    get: (params: GetRecommendationParams) => ["recommendations", "get", params] as const,
  },
} as const;
