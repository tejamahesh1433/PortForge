import type {
  GetRecommendationParams,
  ListAllocationsParams,
  ListConflictsParams,
  ListFleetParams,
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
  allocations: {
    list: (params: ListAllocationsParams = {}) => ["allocations", "list", params] as const,
    detail: (allocationId: string) => ["allocations", "detail", allocationId] as const,
  },
  conflicts: {
    list: (params: ListConflictsParams = {}) => ["conflicts", "list", params] as const,
  },
  recommendations: {
    get: (params: GetRecommendationParams) => ["recommendations", "get", params] as const,
  },
  fleet: {
    list: (params: ListFleetParams = {}) => ["fleet", "list", params] as const,
    detail: (hostId: string) => ["fleet", "detail", hostId] as const,
  },
  upgrades: {
    listForHost: (hostId: string) => ["upgrades", "host", hostId] as const,
    detail: (upgradeId: string) => ["upgrades", "detail", upgradeId] as const,
    status: (upgradeId: string) => ["upgrades", "status", upgradeId] as const,
    hostStatus: (hostId: string) => ["upgrades", "hostStatus", hostId] as const,
  },
} as const;
