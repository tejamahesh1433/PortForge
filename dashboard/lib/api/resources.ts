import { portforgeFetch } from "./client";
import type {
  CentralRecommendationOut,
  ConflictOut,
  GetRecommendationParams,
  HealthOut,
  GlobalDiagnosticsOut,
  HostDiagnosticsOut,
  HostOut,
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
