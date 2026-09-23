import { portforgeFetch, PortForgeApiError, PortForgeConnectionError } from "./client";
import type {
  AllocationOut,
  CentralRecommendationOut,
  ConflictOut,
  GetRecommendationParams,
  HealthOut,
  GlobalDiagnosticsOut,
  HostDiagnosticsOut,
  HostOut,
  ListAllocationsParams,
  ListConflictsParams,
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
