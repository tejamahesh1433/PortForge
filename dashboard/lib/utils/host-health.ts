import type { HostOut } from "@/lib/types/api";

export type HostHealthState = "HEALTHY" | "STALE" | "OFFLINE" | "DEGRADED";

const STALE_AFTER_MS = 120_000;
const OFFLINE_AFTER_MS = 300_000;
const HEALTH_STATES = new Set<HostHealthState>(["HEALTHY", "STALE", "OFFLINE", "DEGRADED"]);

export function getHostHealthState(
  host: Pick<HostOut, "last_seen"> & { health_state?: string },
  now: Date = new Date(),
): HostHealthState {
  if (host.health_state && HEALTH_STATES.has(host.health_state as HostHealthState)) {
    return host.health_state as HostHealthState;
  }

  const lastSeen = new Date(host.last_seen).getTime();
  if (Number.isNaN(lastSeen)) return "OFFLINE";

  const age = Math.max(0, now.getTime() - lastSeen);
  if (age > OFFLINE_AFTER_MS) return "OFFLINE";
  if (age > STALE_AFTER_MS) return "STALE";
  return "HEALTHY";
}