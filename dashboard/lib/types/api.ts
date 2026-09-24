/**
 * TypeScript mirrors of the real Central FastAPI Pydantic schemas.
 *
 * Every interface here corresponds exactly to a schema in
 * backend/app/schemas/*.py -- field names, optionality, and shapes are
 * copied from the actual backend source, not invented. When the backend
 * schema changes, this file must be updated to match; it is never the
 * other way around.
 *
 * Source of truth (for humans verifying this file later):
 *   - backend/app/schemas/common.py   -> Page<T>
 *   - backend/app/schemas/health.py   -> HealthOut
 *   - backend/app/schemas/host.py     -> HostOut
 *   - backend/app/schemas/port.py     -> PortObservationOut
 *   - backend/app/schemas/project.py  -> ProjectOut, ProjectServiceEntry
 *   - backend/app/schemas/reservation.py -> ReservationOut
 *   - backend/app/schemas/conflict.py -> ConflictOut
 *   - backend/app/schemas/recommendation.py -> CentralRecommendationOut
 *   - backend/app/schemas/allocation.py -> AllocationOut, AllocationEntryOut
 */

/** backend/app/schemas/common.py:Page */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** backend/app/schemas/health.py:HealthOut */
export interface HealthOut {
  status: string;
  service: string;
  database: string;
  version: string;
}

/**
 * backend/app/schemas/host.py:HostOut
 *
 * Note: `status` is written as "online" at enrollment/every heartbeat and
 * is never flipped to "offline" server-side (see
 * backend/app/repositories/host_repository.py:upsert -- there is no
 * background job that revisits it). It is NOT a reliable liveness signal
 * on its own. The dashboard derives online/offline from `last_seen`
 * recency instead -- see lib/utils/host-status.ts. The raw field is kept
 * here and surfaced for transparency, never discarded.
 */
export interface HostOut {
  id: string;
  hostname: string;
  display_name: string | null;
  operating_system: string;
  os_version: string | null;
  architecture: string | null;
  agent_version: string | null;
  docker_available: boolean;
  first_seen: string;
  last_seen: string;
  status: string;

  // Derived health fields (Phase 7C.2)
  health_state?: "HEALTHY" | "STALE" | "OFFLINE" | "DEGRADED";
  health_reason?: string;
  age_seconds?: number;
  snapshot_age_seconds?: number | null;

  // v1.1-D: raw last-reported protocol_version (null = legacy agent that
  // never sent one, or never yet contacted Central since v1.1-A shipped).
  // protocol_compatibility is always recomputed live server-side from it --
  // never treat it as a permanent verdict.
  protocol_version?: number | null;
  protocol_compatibility?: "compatible" | "warning" | "unknown";

  // Phase 8: host decommission lifecycle.
  // Missing lifecycle_state is treated as ACTIVE for back-compat with older
  // Central versions that do not yet emit this field.
  lifecycle_state?: "ACTIVE" | "DECOMMISSIONED";
  decommissioned_at?: string | null;
  decommission_reason?: string | null;
}

export interface HostDiagnosticsOut {
  host: HostOut;
  last_scan_observed_at: string | null;
  stale_threshold_seconds: number;
  offline_threshold_seconds: number;
  /** v1.1-D: "unknown" covers both "never asked" and "legacy agent" --
   * Central cannot honestly distinguish the two from data alone. */
  probe_capability?: "supported" | "unknown" | "unavailable_offline";
}

export interface GlobalDiagnosticsOut {
  status: string;
  service: string;
  database: string;
  version: string;
  host_count_total: number;
  host_count_healthy: number;
  host_count_stale: number;
  host_count_offline: number;
  latest_ingestion_time: string | null;
}

/** backend/app/schemas/port.py:PortObservationOut */
export interface PortObservationOut {
  id: string;
  host_id: string;
  host_hostname: string | null;

  port: number;
  protocol: string;
  bind_address: string;
  state: string;
  source: string;

  pid: number | null;
  process_name: string | null;
  process_path: string | null;
  working_directory: string | null;

  container_id: string | null;
  container_name: string | null;
  container_image: string | null;
  container_port: number | null;

  docker_compose_project: string | null;
  service_name: string | null;

  project_name: string | null;
  purpose: string | null;
  category: string | null;
  detection_confidence: string | null;

  first_seen: string;
  last_seen: string;
  observed_at: string;
}

/** backend/app/schemas/project.py:ProjectServiceEntry */
export interface ProjectServiceEntry {
  host_id: string;
  hostname: string;
  port: number;
  protocol: string;
  service_name: string | null;
  purpose: string | null;
  category: string | null;
  state: string;
}

/** backend/app/schemas/project.py:ProjectOut */
export interface ProjectOut {
  project_name: string;
  host_count: number;
  port_count: number;
  process_count: number;
  docker_binding_count: number;
  container_count: number;
  reservation_count: number;
  conflict_count: number;
  healthy_host_count: number;
  stale_host_count: number;
  offline_host_count: number;
  last_activity: string | null;
  hosts: string[];
  entries: ProjectServiceEntry[];
}

export interface ProjectHostOut {
  host_id: string;
  hostname: string;
  operating_system: string;
  docker_available: boolean;
  health_state: "HEALTHY" | "STALE" | "OFFLINE" | "DEGRADED";
  health_reason: string;
  age_seconds: number;
  snapshot_age_seconds: number | null;
  binding_count: number;
}

export interface ProjectDetailOut extends ProjectOut {
  host_details: ProjectHostOut[];
  ports: Page<PortObservationOut>;
  reservations: Page<ReservationOut>;
  conflicts: ConflictOut[];
  activity: ActivityEventOut[];
}

/** backend/app/schemas/reservation.py:ReservationOut */
export interface ReservationOut {
  id: string;
  host_id: string;
  port: number;
  protocol: string;
  bind_address: string | null;
  project: string;
  service: string | null;
  purpose: string | null;
  notes: string | null;
  local_reservation_id: string | null;
  /** Phase 8A: set when this reservation was created as part of an agent allocation bundle. */
  allocation_id: string | null;
  request_name: string | null;
  created_at: string;
  updated_at: string;
}

/** backend/app/schemas/reservation.py:DashboardReservationIn */
export interface DashboardReservationIn {
  host_id: string;
  port: number;
  protocol: string;
  bind_address?: string | null;
  project: string;
  service?: string | null;
  purpose?: string | null;
  notes?: string | null;
}

/** backend/app/schemas/conflict.py:ConflictOut */
export interface ConflictOut {
  host_id: string;
  hostname: string;
  port: number;
  protocol: string;
  reserved_for_project: string;
  reserved_for_service: string | null;
  actual_project: string | null;
  actual_process_name: string | null;
  actual_container_name: string | null;
  reason: string;
}

/** backend/app/schemas/recommendation.py:CentralRecommendationOut */
export type VerificationLevel = "central_suggestion" | "locally_verified";

export interface CentralRecommendationOut {
  service_type: string;
  protocol: string;
  recommended_port: number | null;
  verification: VerificationLevel;
  basis: string;
  candidates_considered: number;
  known_conflicts_excluded: number[];
}

/** backend/app/schemas/allocation.py:AllocationHostOut */
export interface AllocationHostOut {
  id: string;
  hostname: string;
}

/**
 * backend/app/schemas/allocation.py:AllocationEntryOut
 *
 * `bind_probe` is computed LIVE at read time from v1.1-B evidence, never a
 * stored creation-time snapshot -- see docs/v1.1/v1.1-d-data-audit.md.
 * Same 5-value contract as AllocationValidationOut.bind_probe below.
 */
export interface AllocationEntryOut {
  name: string;
  purpose: string;
  protocol: string;
  port: number;
  reservation_id: string;
  requested_range?: string | null;
  bind_address: string | null;
  bind_probe: BindProbeEvidence;
}

/**
 * backend/app/services/probe_service.py's 5-value bind_probe contract.
 * CRITICAL (v1.1-D task Sec5/Sec32): "verified_free" means PortForge
 * verified this binding was free ON THE TARGET HOST AT PROBE TIME. It is
 * NEVER a guarantee the port is currently free -- an unmanaged process on
 * that host can still bind it after the probe ran. Never present it as a
 * permanent guarantee anywhere in the UI.
 */
export type BindProbeEvidence =
  | "not_remote_capable"
  | "verified_free"
  | "verified_occupied"
  | "unavailable"
  | "expired";

export interface AllocationRequestItemIn {
  name: string;
  purpose: string;
  protocol?: "tcp" | "udp";
  preferred_port?: number;
  requested_range?: string;
}

export interface AllocationIn {
  project: string;
  host_id: string;
  requests: AllocationRequestItemIn[];
  request_id?: string;
}

/** backend/app/schemas/allocation.py:AllocationValidationOut */
export interface AllocationValidationOut {
  snapshot_age_seconds: number;
  host_health_state: string;
  bind_probe: BindProbeEvidence;
}

/** backend/app/schemas/allocation.py:AllocationOut */
export interface AllocationOut {
  idempotent_replay: boolean;
  allocation_id: string;
  project: string;
  host: AllocationHostOut;
  status: "active" | "released";
  allocations: AllocationEntryOut[];
  validation: AllocationValidationOut;
  created_at: string;
  released_at: string | null;
  request_id: string | null;
}

/** Query params accepted by GET /api/allocations (backend/app/api/allocations.py) */
export interface ListAllocationsParams {
  host_id?: string;
  project?: string;
  status?: "active" | "released";
  search?: string;
  limit?: number;
  offset?: number;
}

/** Query params accepted by GET /api/hosts (backend/app/api/hosts.py) */
export interface ListHostsParams {
  limit?: number;
  offset?: number;
}

/** Query params accepted by GET /api/ports (backend/app/api/ports.py) */
export interface ListPortsParams {
  port?: number;
  project?: string;
  purpose?: string;
  source?: string;
  limit?: number;
  offset?: number;
}

/** Query params accepted by GET /api/reservations (backend/app/api/reservations.py) */
export interface ListReservationsParams {
  host_id?: string;
  port?: number;
  project?: string;
  limit?: number;
  offset?: number;
}

/** Query params accepted by GET /api/conflicts (backend/app/api/conflicts.py) */
export interface ListConflictsParams {
  host_id?: string;
}

/** Query params accepted by GET /api/recommendations (backend/app/api/recommendations.py) */
export interface GetRecommendationParams {
  host_id: string;
  service_type: string;
  protocol?: "tcp" | "udp";
}

/** backend/app/schemas/activity.py:ActivityEventOut */
export interface ActivityEventOut {
  id: string;
  host_id: string;
  timestamp: string;
  event_type: string;

  port: number | null;
  protocol: string | null;
  bind_address: string | null;

  source: string | null;
  identity_context: string | null;
  reservation_id: string | null;

  summary: string;
  metadata_json: Record<string, unknown> | null;
}

export interface ActivityResponse {
  events: ActivityEventOut[];
  total: number;
}

/** Query params accepted by GET /api/activity (backend/app/api/activity.py) */
export interface ListActivityParams {
  host_id?: string;
  event_type?: string;
  port?: number;
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// Phase 9/10: Fleet intelligence + agent upgrade management
// backend/app/schemas/fleet.py + backend/app/schemas/upgrade.py
// ---------------------------------------------------------------------------

/**
 * backend/app/schemas/fleet.py:UpdateAvailability
 *
 * CURRENT: agent_version == configured target
 * UPDATE_AVAILABLE: agent_version < target (semantic semver comparison)
 * UNSUPPORTED: agent_version > target (unexpected downgrade not offered)
 * UNKNOWN: no target configured, missing agent_version, or unparseable version
 */
export type UpdateAvailability = "CURRENT" | "UPDATE_AVAILABLE" | "UNSUPPORTED" | "UNKNOWN";

/**
 * backend/app/schemas/upgrade.py:UpgradeState
 *
 * Terminal states: SUCCEEDED, FAILED, ROLLED_BACK
 * Non-terminal: all others
 */
export type UpgradeState =
  | "APPROVED"
  | "WAITING_FOR_AGENT"
  | "DOWNLOADING"
  | "VERIFYING"
  | "INSTALLING"
  | "RESTARTING"
  | "VERIFYING_HEALTH"
  | "SUCCEEDED"
  | "FAILED"
  | "ROLLED_BACK";

/** Minimal upgrade summary embedded in FleetHostOut.active_upgrade */
export interface ActiveUpgradeSummary {
  id: string;
  state: UpgradeState;
  target_version: string;
  // Phase 22: additive observability fields; absent on older Central versions.
  progress_status?: UpgradeProgressStatus | null;
  waiting_reason?: string | null;
  failure_code?: string | null;
  explanation?: string | null;
  operator_actions?: UpgradeOperatorAction[];
}

/**
 * backend/app/schemas/fleet.py:FleetHostOut
 *
 * Extends host facts with fleet-specific fields.
 */
export interface FleetHostOut {
  id: string;
  hostname: string;
  display_name: string | null;
  operating_system: string;
  os_version: string | null;
  architecture: string | null;
  agent_version: string | null;
  docker_available: boolean;
  first_seen: string;
  last_seen: string;
  status: string;

  health_state?: "HEALTHY" | "STALE" | "OFFLINE" | "DEGRADED";
  health_reason?: string;
  age_seconds?: number;

  lifecycle_state?: "ACTIVE" | "DECOMMISSIONED";
  decommissioned_at?: string | null;
  decommission_reason?: string | null;

  protocol_version?: number | null;
  protocol_compatibility?: "compatible" | "warning" | "unknown";

  // Phase 9 fleet fields
  last_heartbeat: string;
  last_sync: string | null;
  update_availability: UpdateAvailability;
  target_version: string | null;
  active_upgrade: ActiveUpgradeSummary | null;

  // Optional new columns (null if the agent hasn't reported them yet)
  contract_version: number | null;
  python_version: string | null;
  last_error: string | null;
}

/** Query params accepted by GET /api/fleet */
export interface ListFleetParams {
  q?: string;
  lifecycle_state?: "ACTIVE" | "DECOMMISSIONED";
  health_state?: "HEALTHY" | "STALE" | "OFFLINE" | "DEGRADED";
  update_availability?: UpdateAvailability;
  limit?: number;
  offset?: number;
}

/**
 * backend/app/schemas/upgrade.py:UpgradeOut
 */
export interface UpgradeOut {
  id: string;
  host_id: string;
  state: UpgradeState;
  target_version: string;
  artifact_url: string;
  artifact_sha256: string;
  artifact_filename: string | null;
  request_id: string | null;
  failure_reason: string | null;
  previous_version: string | null;
  previous_artifact_url: string | null;
  previous_artifact_sha256: string | null;
  created_at: string;
  updated_at: string;
}

/** Body for POST /api/hosts/{host_id}/upgrades (admin-only) */
export interface CreateUpgradeIn {
  target_version: string;
  artifact_url: string;
  artifact_sha256: string;
  artifact_filename?: string | null;
  request_id?: string | null;
}

// ---------------------------------------------------------------------------
// Phase 22: Upgrade observability types
// backend/app/schemas/upgrade.py -- additive fields; older Central versions
// that do not emit them will simply omit the keys (treated as null/[]).
// ---------------------------------------------------------------------------

/**
 * High-level progress category returned by GET /api/upgrades/{id}/status.
 * "waiting"     – awaiting next agent heartbeat
 * "in_progress" – actively executing a step
 * "blocked"     – stalled (waiting_reason will say why)
 * "done"        – terminal success
 * "failed"      – terminal failure
 */
export type UpgradeProgressStatus =
  | "waiting"
  | "in_progress"
  | "blocked"
  | "done"
  | "failed";

/**
 * Operator-visible actions the backend considers safe for the current state.
 * The UI only renders a button when the action appears in operator_actions.
 * "retry"  – re-attempt the same upgrade
 * "cancel" – abort an in-progress or waiting upgrade
 */
export type UpgradeOperatorAction = "VIEW" | "RETRY" | "CANCEL" | "ROLLBACK";

/**
 * backend/app/schemas/upgrade.py:UpgradeStatusOut
 * Returned by GET /api/upgrades/{id}/status (admin-gated via BFF).
 */
export interface UpgradeStatusOut {
  id: string;
  host_id: string;
  state: UpgradeState;
  target_version: string;
  progress_status: UpgradeProgressStatus | null;
  waiting_reason: string | null;
  failure_code: string | null;
  explanation: string | null;
  operator_actions: UpgradeOperatorAction[];
  attempt_number: number | null;
  attempt_max: number | null;
  reconciliation_status: string | null;
  /** Host health at status-poll time, separate from upgrade_state. */
  host_health: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * backend/app/schemas/fleet.py:RolloutOut
 * Fleet-wide rollout operation (Phase 22, additive fields).
 */
export interface RolloutOut {
  id: string;
  state: string;
  target_version: string;
  stop_reason?: string | null;
  operator_summary?: string | null;
  not_started?: boolean;
}
