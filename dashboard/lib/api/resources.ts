import { portforgeFetch, PortForgeApiError, PortForgeConnectionError } from "./client";
import type {
  AllocationOut,
  CentralRecommendationOut,
  ConflictOut,
  CreateUpgradeIn,
  FleetHostOut,
  GetRecommendationParams,
  HealthOut,
  GlobalDiagnosticsOut,
  HostDiagnosticsOut,
  HostOut,
  ListAllocationsParams,
  ListConflictsParams,
  ListFleetParams,
  ListHostsParams,
  ListPortsParams,
  ListReservationsParams,
  Page,
  PortObservationOut,
  ProjectOut,
  ProjectDetailOut,
  ReservationOut,
  DashboardReservationIn,
  AllocationIn,
  UpgradeOut,
  UpgradeStatusOut,
} from "@/lib/types/api";

/** GET /api/health */
export function getHealth(signal?: AbortSignal): Promise<HealthOut> {
  return portforgeFetch<HealthOut>("/api/health", { signal });
}

/** GET /api/diagnostics */
export function getGlobalDiagnostics(signal?: AbortSignal): Promise<GlobalDiagnosticsOut> {
  return portforgeFetch<GlobalDiagnosticsOut>("/api/diagnostics", { signal });
}

/** GET /api/hosts/{host_id}/diagnostics */
export function getHostDiagnostics(hostId: string, signal?: AbortSignal): Promise<HostDiagnosticsOut> {
  return portforgeFetch<HostDiagnosticsOut>(`/api/hosts/${hostId}/diagnostics`, { signal });
}

/** GET /api/hosts */
export function listHosts(
  params: ListHostsParams = {},
  signal?: AbortSignal,
): Promise<Page<HostOut>> {
  return portforgeFetch<Page<HostOut>>("/api/hosts", { params, signal });
}

/** GET /api/hosts/{host_id} */
export function getHost(hostId: string, signal?: AbortSignal): Promise<HostOut> {
  return portforgeFetch<HostOut>(`/api/hosts/${hostId}`, { signal });
}

/** Remove Record via dashboard BFF (admin secret stays server-side). */
export async function deleteHost(hostId: string, signal?: AbortSignal): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api/hosts/${encodeURIComponent(hostId)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  if (response.status === 204) {
    return;
  }

  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = typeof body?.detail === "string" ? body.detail : null;
  throw new PortForgeApiError(
    response.status,
    detail,
    detail ?? `Failed to remove host (${response.status})`,
  );
}

/** Decommission host via dashboard BFF (admin secret stays server-side). */
export async function decommissionHost(
  hostId: string,
  body?: { reason?: string },
  signal?: AbortSignal,
): Promise<HostOut> {
  let response: Response;
  try {
    response = await fetch(`/api/hosts/${encodeURIComponent(hostId)}/decommission`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  const responseBody = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof responseBody?.detail === "string" ? responseBody.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to decommission host (${response.status})`,
    );
  }
  return responseBody as HostOut;
}

/** Reactivate host via dashboard BFF (admin secret stays server-side). */
export async function reactivateHost(hostId: string, signal?: AbortSignal): Promise<HostOut> {
  let response: Response;
  try {
    response = await fetch(`/api/hosts/${encodeURIComponent(hostId)}/reactivate`, {
      method: "POST",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  const responseBody = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof responseBody?.detail === "string" ? responseBody.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to reactivate host (${response.status})`,
    );
  }
  return responseBody as HostOut;
}

/** GET /api/hosts/{host_id}/ports */
export function getHostPorts(
  hostId: string,
  signal?: AbortSignal,
): Promise<PortObservationOut[]> {
  return portforgeFetch<PortObservationOut[]>(`/api/hosts/${hostId}/ports`, { signal });
}

/** GET /api/ports */
export function listPorts(
  params: ListPortsParams = {},
  signal?: AbortSignal,
): Promise<Page<PortObservationOut>> {
  return portforgeFetch<Page<PortObservationOut>>("/api/ports", { params, signal });
}

/** GET /api/projects */
export function listProjects(signal?: AbortSignal): Promise<ProjectOut[]> {
  return portforgeFetch<ProjectOut[]>("/api/projects", { signal });
}

/** GET /api/projects/{project_name} */
export function getProject(projectName: string, signal?: AbortSignal): Promise<ProjectDetailOut> {
  return portforgeFetch<ProjectDetailOut>(`/api/projects/${encodeURIComponent(projectName)}`, { signal });
}

/** GET /api/reservations */
export function listReservations(
  params: ListReservationsParams = {},
  signal?: AbortSignal,
): Promise<Page<ReservationOut>> {
  return portforgeFetch<Page<ReservationOut>>("/api/reservations", { params, signal });
}

/** POST /api/reservations/dashboard */
export function createDashboardReservation(
  payload: DashboardReservationIn,
  signal?: AbortSignal,
): Promise<ReservationOut> {
  return portforgeFetch<ReservationOut>("/api/reservations/dashboard", {
    method: "POST",
    body: payload,
    signal,
  });
}

/** DELETE /api/reservations/dashboard/{host_id}/{reservation_id} */
export function deleteDashboardReservation(
  hostId: string,
  reservationId: string,
  signal?: AbortSignal,
): Promise<void> {
  return portforgeFetch<void>(`/api/reservations/dashboard/${hostId}/${reservationId}`, {
    method: "DELETE",
    signal,
  });
}

/** POST /api/allocations */
export function createAllocation(payload: AllocationIn, signal?: AbortSignal): Promise<AllocationOut> {
  return portforgeFetch<AllocationOut>("/api/allocations", { method: "POST", body: payload, signal });
}

/** GET /api/allocations */
export function listAllocations(
  params: ListAllocationsParams = {},
  signal?: AbortSignal,
): Promise<Page<AllocationOut>> {
  return portforgeFetch<Page<AllocationOut>>("/api/allocations", { params, signal });
}

/** POST /api/allocations/{allocation_id}/verify */
export function verifyAllocation(allocationId: string, signal?: AbortSignal): Promise<AllocationOut> {
  return portforgeFetch<AllocationOut>("/api/allocations/" + allocationId + "/verify", { method: "POST", signal });
}

/** GET /api/allocations/{allocation_id} */
export function getAllocation(allocationId: string, signal?: AbortSignal): Promise<AllocationOut> {
  return portforgeFetch<AllocationOut>(`/api/allocations/${allocationId}`, { signal });
}

/** DELETE /api/allocations/{allocation_id} -- uses the real allocation
 * release contract (Phase 8A), not individual reservation deletion. */
export function releaseAllocation(allocationId: string, signal?: AbortSignal): Promise<AllocationOut> {
  return portforgeFetch<AllocationOut>(`/api/allocations/${allocationId}`, { method: "DELETE", signal });
}

/** GET /api/conflicts */
export function listConflicts(
  params: ListConflictsParams = {},
  signal?: AbortSignal,
): Promise<ConflictOut[]> {
  return portforgeFetch<ConflictOut[]>("/api/conflicts", { params, signal });
}

/**
 * GET /api/recommendations
 *
 * Always a "central_suggestion" (see backend/app/schemas/recommendation.py's
 * module docstring) -- Central never re-runs discovery or a live bind
 * probe. Requires host_id + service_type, so this is only ever called
 * with a concrete target in mind (see app/recommendations/page.tsx).
 */
export function getRecommendation(
  params: GetRecommendationParams,
  signal?: AbortSignal,
): Promise<CentralRecommendationOut> {
  return portforgeFetch<CentralRecommendationOut>("/api/recommendations", { params, signal });
}

// ---------------------------------------------------------------------------
// Phase 9/10: Fleet + Upgrade APIs
// ---------------------------------------------------------------------------

/** GET /api/fleet -- fleet-intelligence view (read-open like /api/hosts). */
export function listFleet(
  params: ListFleetParams = {},
  signal?: AbortSignal,
): Promise<Page<FleetHostOut>> {
  return portforgeFetch<Page<FleetHostOut>>("/api/fleet", { params, signal });
}

/** GET /api/fleet/{host_id} -- single fleet host detail. */
export function getFleetHost(hostId: string, signal?: AbortSignal): Promise<FleetHostOut> {
  return portforgeFetch<FleetHostOut>(`/api/fleet/${hostId}`, { signal });
}

/**
 * POST /api/hosts/{hostId}/upgrades (admin) -- goes through the dashboard
 * BFF so the admin token never leaves the server.
 */
export async function createHostUpgrade(
  hostId: string,
  payload: CreateUpgradeIn,
  signal?: AbortSignal,
): Promise<UpgradeOut> {
  let response: Response;
  try {
    response = await fetch(`/api/hosts/${encodeURIComponent(hostId)}/upgrades`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to create upgrade (${response.status})`,
    );
  }
  return body as UpgradeOut;
}

/** GET /api/hosts/{hostId}/upgrades -- list upgrades for a host. */
export function listHostUpgrades(hostId: string, signal?: AbortSignal): Promise<UpgradeOut[]> {
  return portforgeFetch<UpgradeOut[]>(`/api/hosts/${hostId}/upgrades`, { signal });
}

/** GET /api/upgrades/{id} -- single upgrade detail. */
export function getUpgrade(upgradeId: string, signal?: AbortSignal): Promise<UpgradeOut> {
  return portforgeFetch<UpgradeOut>(`/api/upgrades/${upgradeId}`, { signal });
}

/**
 * POST /api/upgrades/{id}/rollback (admin) -- goes through the dashboard
 * BFF so the admin token never leaves the server.
 */
export async function rollbackUpgrade(upgradeId: string, signal?: AbortSignal): Promise<UpgradeOut> {
  let response: Response;
  try {
    response = await fetch(`/api/upgrades/${encodeURIComponent(upgradeId)}/rollback`, {
      method: "POST",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to rollback upgrade (${response.status})`,
    );
  }
  return body as UpgradeOut;
}

// ---------------------------------------------------------------------------
// Phase 22: Upgrade observability (all admin-gated via BFF)
// ---------------------------------------------------------------------------

/**
 * GET /api/upgrades/{id}/status (admin via BFF) -- typed status with
 * progress_status, waiting_reason, failure_code, operator_actions, etc.
 */
export async function getUpgradeStatus(
  upgradeId: string,
  signal?: AbortSignal,
): Promise<UpgradeStatusOut> {
  let response: Response;
  try {
    response = await fetch(`/api/upgrades/${encodeURIComponent(upgradeId)}/status`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to get upgrade status (${response.status})`,
    );
  }
  return body as UpgradeStatusOut;
}

/**
 * POST /api/upgrades/{id}/retry (admin via BFF) -- re-attempt a failed upgrade.
 */
export async function retryUpgrade(upgradeId: string, signal?: AbortSignal): Promise<UpgradeOut> {
  let response: Response;
  try {
    response = await fetch(`/api/upgrades/${encodeURIComponent(upgradeId)}/retry`, {
      method: "POST",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to retry upgrade (${response.status})`,
    );
  }
  return body as UpgradeOut;
}

/**
 * POST /api/upgrades/{id}/cancel (admin via BFF) -- abort an in-progress upgrade.
 */
export async function cancelUpgrade(upgradeId: string, signal?: AbortSignal): Promise<UpgradeOut> {
  let response: Response;
  try {
    response = await fetch(`/api/upgrades/${encodeURIComponent(upgradeId)}/cancel`, {
      method: "POST",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to cancel upgrade (${response.status})`,
    );
  }
  return body as UpgradeOut;
}

/**
 * GET /api/hosts/{hostId}/upgrade-status (admin via BFF) -- host-level
 * upgrade status, including the active upgrade's typed status if any.
 * Returns null (404 treated as no active upgrade) rather than throwing.
 */
export async function getHostUpgradeStatus(
  hostId: string,
  signal?: AbortSignal,
): Promise<UpgradeStatusOut | null> {
  let response: Response;
  try {
    response = await fetch(`/api/hosts/${encodeURIComponent(hostId)}/upgrade-status`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  if (response.status === 404) {
    return null;
  }

  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Failed to get host upgrade status (${response.status})`,
    );
  }
  return body as UpgradeStatusOut;
}
