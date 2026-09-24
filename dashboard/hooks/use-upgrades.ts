"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createHostUpgrade,
  listHostUpgrades,
  getUpgrade,
  rollbackUpgrade,
  getUpgradeStatus,
  retryUpgrade,
  cancelUpgrade,
  getHostUpgradeStatus,
} from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { CreateUpgradeIn } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/** GET /api/hosts/{hostId}/upgrades */
export function useHostUpgrades(hostId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.upgrades.listForHost(hostId ?? ""),
    queryFn: ({ signal }) => listHostUpgrades(hostId as string, signal),
    enabled: Boolean(hostId),
    staleTime: STALE_TIME_MS,
  });
}

/** GET /api/upgrades/{upgradeId} */
export function useUpgrade(upgradeId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.upgrades.detail(upgradeId ?? ""),
    queryFn: ({ signal }) => getUpgrade(upgradeId as string, signal),
    enabled: Boolean(upgradeId),
    staleTime: STALE_TIME_MS,
  });
}

/** POST /api/hosts/{hostId}/upgrades (admin via BFF) */
export function useCreateHostUpgrade(hostId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateUpgradeIn) => createHostUpgrade(hostId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.listForHost(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.detail(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.list() });
    },
  });
}

/** POST /api/upgrades/{upgradeId}/rollback (admin via BFF) */
export function useRollbackUpgrade(upgradeId: string, hostId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => rollbackUpgrade(upgradeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.detail(upgradeId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.listForHost(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.detail(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.list() });
    },
  });
}

// ---------------------------------------------------------------------------
// Phase 22: Upgrade observability hooks
// ---------------------------------------------------------------------------

/**
 * GET /api/upgrades/{upgradeId}/status (admin via BFF).
 * Only enabled when upgradeId is provided and the upgrade is non-terminal
 * (callers pass `enabled` accordingly).
 */
export function useUpgradeStatus(upgradeId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.upgrades.status(upgradeId ?? ""),
    queryFn: ({ signal }) => getUpgradeStatus(upgradeId as string, signal),
    enabled: Boolean(upgradeId) && enabled,
    staleTime: STALE_TIME_MS,
  });
}

/** GET /api/hosts/{hostId}/upgrade-status (admin via BFF). */
export function useHostUpgradeStatus(hostId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.upgrades.hostStatus(hostId ?? ""),
    queryFn: ({ signal }) => getHostUpgradeStatus(hostId as string, signal),
    enabled: Boolean(hostId),
    staleTime: STALE_TIME_MS,
  });
}

/** POST /api/upgrades/{upgradeId}/retry (admin via BFF) */
export function useRetryUpgrade(upgradeId: string, hostId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => retryUpgrade(upgradeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.detail(upgradeId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.status(upgradeId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.listForHost(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.detail(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.list() });
    },
  });
}

/** POST /api/upgrades/{upgradeId}/cancel (admin via BFF) */
export function useCancelUpgrade(upgradeId: string, hostId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => cancelUpgrade(upgradeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.detail(upgradeId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.status(upgradeId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.upgrades.listForHost(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.detail(hostId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.list() });
    },
  });
}
