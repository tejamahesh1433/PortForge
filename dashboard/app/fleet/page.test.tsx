/**
 * Fleet page: client-side filter logic tests.
 *
 * We test the filtering predicate in isolation (same pattern as hosts/page
 * tests) rather than rendering the full Suspense + router stack.
 */
import { describe, expect, it } from "vitest";
import type { FleetHostOut, UpdateAvailability } from "@/lib/types/api";

// ---------------------------------------------------------------------------
// Filtering predicate (copy of what FleetPageContent.filtered does)
// ---------------------------------------------------------------------------

type HealthState = "HEALTHY" | "STALE" | "OFFLINE" | "DEGRADED";

function applyFilters(
  items: FleetHostOut[],
  opts: {
    search?: string;
    lifecycleFilter?: string;
    healthFilter?: string;
    updateFilter?: string;
    diagnosticsFilter?: string;
  },
) {
  const {
    search = "",
    lifecycleFilter = "all",
    healthFilter = "all",
    updateFilter = "all",
    diagnosticsFilter = "all",
  } = opts;

  const query = search.trim().toLowerCase();
  return items.filter((host) => {
    if (query && !host.hostname.toLowerCase().includes(query)) return false;
    if (lifecycleFilter !== "all" && (host.lifecycle_state ?? "ACTIVE") !== lifecycleFilter) return false;
    if (healthFilter !== "all" && host.health_state !== (healthFilter as HealthState)) return false;
    if (updateFilter !== "all" && host.update_availability !== (updateFilter as UpdateAvailability)) return false;
    if (diagnosticsFilter === "problems") {
      const hasIssue =
        Boolean(host.last_error) ||
        host.health_state === "OFFLINE" ||
        host.health_state === "DEGRADED";
      if (!hasIssue) return false;
    }
    return true;
  });
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeHost(overrides: Partial<FleetHostOut> = {}): FleetHostOut {
  return {
    id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    hostname: "lab-linux",
    display_name: null,
    operating_system: "linux",
    os_version: null,
    architecture: "x86_64",
    agent_version: "1.1.0",
    docker_available: false,
    first_seen: "2026-09-01T00:00:00Z",
    last_seen: new Date().toISOString(),
    status: "online",
    health_state: "HEALTHY",
    age_seconds: 30,
    lifecycle_state: "ACTIVE",
    decommissioned_at: null,
    decommission_reason: null,
    protocol_version: 1,
    protocol_compatibility: "compatible",
    last_heartbeat: new Date().toISOString(),
    last_sync: null,
    update_availability: "CURRENT",
    target_version: "1.1.0",
    active_upgrade: null,
    contract_version: null,
    python_version: null,
    last_error: null,
    ...overrides,
  };
}

const activeHealthy = makeHost({ hostname: "alpha", update_availability: "CURRENT" });
const activeOutdated = makeHost({
  id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
  hostname: "beta",
  update_availability: "UPDATE_AVAILABLE",
  agent_version: "1.0.0",
  target_version: "1.1.0",
});
const decommissionedHost = makeHost({
  id: "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa",
  hostname: "gamma",
  lifecycle_state: "DECOMMISSIONED",
  health_state: "OFFLINE",
  update_availability: "UNKNOWN",
});
const offlineWithError = makeHost({
  id: "dddddddd-eeee-ffff-aaaa-bbbbbbbbbbbb",
  hostname: "delta",
  health_state: "OFFLINE",
  update_availability: "UNKNOWN",
  last_error: "Agent crashed: exit code 1",
});

const ALL = [activeHealthy, activeOutdated, decommissionedHost, offlineWithError];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Fleet page client-side filters", () => {
  it("no filters returns all items", () => {
    expect(applyFilters(ALL, {})).toHaveLength(4);
  });

  it("search filters by hostname substring case-insensitively", () => {
    const result = applyFilters(ALL, { search: "ALPHA" });
    expect(result).toHaveLength(1);
    expect(result[0].hostname).toBe("alpha");
  });

  it("lifecycle filter ACTIVE excludes decommissioned hosts", () => {
    const result = applyFilters(ALL, { lifecycleFilter: "ACTIVE" });
    expect(result.every((h) => (h.lifecycle_state ?? "ACTIVE") === "ACTIVE")).toBe(true);
    expect(result.some((h) => h.hostname === "gamma")).toBe(false);
  });

  it("lifecycle filter DECOMMISSIONED returns only decommissioned hosts", () => {
    const result = applyFilters(ALL, { lifecycleFilter: "DECOMMISSIONED" });
    expect(result).toHaveLength(1);
    expect(result[0].hostname).toBe("gamma");
  });

  it("update filter UPDATE_AVAILABLE returns only outdated hosts", () => {
    const result = applyFilters(ALL, { updateFilter: "UPDATE_AVAILABLE" });
    expect(result).toHaveLength(1);
    expect(result[0].hostname).toBe("beta");
  });

  it("health filter OFFLINE returns offline hosts", () => {
    const result = applyFilters(ALL, { healthFilter: "OFFLINE" });
    expect(result.every((h) => h.health_state === "OFFLINE")).toBe(true);
    expect(result).toHaveLength(2);
  });

  it("diagnostics filter 'problems' returns hosts with last_error or OFFLINE/DEGRADED health", () => {
    const result = applyFilters(ALL, { diagnosticsFilter: "problems" });
    // decommissionedHost (OFFLINE), offlineWithError (OFFLINE + last_error)
    expect(result).toHaveLength(2);
    expect(result.every((h) => h.health_state === "OFFLINE" || Boolean(h.last_error))).toBe(true);
    expect(result.some((h) => h.hostname === "alpha")).toBe(false);
  });

  it("combined filters apply all predicates", () => {
    // ACTIVE + UPDATE_AVAILABLE should return only beta
    const result = applyFilters(ALL, {
      lifecycleFilter: "ACTIVE",
      updateFilter: "UPDATE_AVAILABLE",
    });
    expect(result).toHaveLength(1);
    expect(result[0].hostname).toBe("beta");
  });

  it("search + lifecycle combination", () => {
    // search 'a' matches alpha, gamma, delta; lifecycle ACTIVE removes gamma
    const result = applyFilters(ALL, { search: "a", lifecycleFilter: "ACTIVE" });
    expect(result.every((h) => (h.lifecycle_state ?? "ACTIVE") === "ACTIVE")).toBe(true);
    expect(result.some((h) => h.hostname === "gamma")).toBe(false);
    expect(result.some((h) => h.hostname === "alpha")).toBe(true);
    expect(result.some((h) => h.hostname === "delta")).toBe(true);
  });

  it("DECOMMISSIONED host cannot appear in UPDATE_AVAILABLE filter", () => {
    // decommissioned host has update_availability UNKNOWN
    const result = applyFilters(ALL, {
      lifecycleFilter: "DECOMMISSIONED",
      updateFilter: "UPDATE_AVAILABLE",
    });
    expect(result).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Phase 22: ActiveUpgradeSummary additive fields (progress_status / waiting_reason)
// These are pure type/data tests -- no DOM render needed.
// ---------------------------------------------------------------------------

describe("ActiveUpgradeSummary Phase 22 additive fields", () => {
  it("host with active_upgrade including progress_status and waiting_reason passes through filter", () => {
    const withRichUpgrade = makeHost({
      hostname: "epsilon",
      update_availability: "UPDATE_AVAILABLE",
      active_upgrade: {
        id: "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc",
        state: "WAITING_FOR_AGENT",
        target_version: "1.2.0",
        progress_status: "waiting",
        waiting_reason: "Awaiting next agent heartbeat",
        operator_actions: ["CANCEL"],
      },
    });
    const result = applyFilters([withRichUpgrade], { updateFilter: "UPDATE_AVAILABLE" });
    expect(result).toHaveLength(1);
    expect(result[0].active_upgrade?.progress_status).toBe("waiting");
    expect(result[0].active_upgrade?.waiting_reason).toBe("Awaiting next agent heartbeat");
  });

  it("host with active_upgrade and failure_code in additive fields is accessible", () => {
    const withFailedUpgrade = makeHost({
      hostname: "zeta",
      active_upgrade: {
        id: "ffffffff-aaaa-bbbb-cccc-dddddddddddd",
        state: "FAILED",
        target_version: "1.2.0",
        progress_status: "failed",
        failure_code: "SHA_MISMATCH",
        explanation: "Artifact hash did not match.",
        operator_actions: ["RETRY"],
      },
    });
    const result = applyFilters([withFailedUpgrade], {});
    expect(result).toHaveLength(1);
    expect(result[0].active_upgrade?.failure_code).toBe("SHA_MISMATCH");
    expect(result[0].active_upgrade?.operator_actions).toContain("RETRY");
  });

  it("older host without additive fields (undefined) does not break filter", () => {
    // Simulates an older Central that omits the new fields
    const legacyHost = makeHost({
      hostname: "legacy",
      active_upgrade: {
        id: "aaaaaaaa-1111-2222-3333-444444444444",
        state: "DOWNLOADING",
        target_version: "1.2.0",
        // No progress_status, waiting_reason, etc.
      },
    });
    const result = applyFilters([legacyHost], {});
    expect(result).toHaveLength(1);
    expect(result[0].active_upgrade?.progress_status).toBeUndefined();
    expect(result[0].active_upgrade?.operator_actions).toBeUndefined();
  });
});
