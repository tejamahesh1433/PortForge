"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { type ColumnDef } from "@tanstack/react-table";
import { ArrowUpCircle, AlertTriangle } from "lucide-react";
import { FilterBar } from "@/components/controls/filter-bar";
import { ActiveFilters } from "@/components/controls/active-filters";
import { FilterSelect } from "@/components/controls/filter-select";
import { SearchInput } from "@/components/controls/search-input";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { PaginationBar } from "@/components/data/pagination-bar";
import { DataTable } from "@/components/data/data-table";
import { LifecycleBadge } from "@/components/status/lifecycle-badge";
import { HostStatus } from "@/components/status/host-status";
import { UpdateAvailabilityBadge } from "@/components/status/update-availability-badge";
import { useFleet } from "@/hooks/use-fleet";
import { formatRelativeTime, formatFriendlyOS } from "@/lib/utils/format";
import type { FleetHostOut, UpdateAvailability } from "@/lib/types/api";

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

const columns: ColumnDef<FleetHostOut, unknown>[] = [
  {
    id: "hostname",
    accessorKey: "hostname",
    header: "Hostname",
    cell: ({ row }) => (
      <Link
        href={`/hosts/${row.original.id}`}
        className="font-medium text-foreground hover:text-primary underline-offset-2 hover:underline"
      >
        {row.original.hostname}
      </Link>
    ),
  },
  {
    id: "lifecycle",
    header: "Lifecycle",
    cell: ({ row }) => <LifecycleBadge state={row.original.lifecycle_state} />,
  },
  {
    id: "health",
    header: "Health",
    cell: ({ row }) => <HostStatus host={row.original} />,
  },
  {
    id: "os",
    header: "OS / Arch",
    cell: ({ row }) => {
      const { operating_system, architecture } = row.original;
      return (
        <span className="text-sm text-muted-foreground font-mono">
          {formatFriendlyOS(operating_system)}
          {architecture ? ` / ${architecture}` : ""}
        </span>
      );
    },
  },
  {
    id: "agent_version",
    header: "Agent version",
    cell: ({ row }) => (
      <span className="font-mono text-xs text-muted-foreground">
        {row.original.agent_version ?? "—"}
      </span>
    ),
  },
  {
    id: "target_version",
    header: "Target",
    cell: ({ row }) => (
      <span className="font-mono text-xs text-muted-foreground">
        {row.original.target_version ?? "—"}
      </span>
    ),
  },
  {
    id: "update_status",
    header: "Update status",
    cell: ({ row }) => <UpdateAvailabilityBadge availability={row.original.update_availability} />,
  },
  {
    id: "active_upgrade",
    header: "Active upgrade",
    cell: ({ row }) => {
      const u = row.original.active_upgrade;
      if (!u) return <span className="text-muted-foreground text-xs">—</span>;
      return (
        <span className="inline-flex items-center gap-1 text-xs font-mono text-amber-400">
          <ArrowUpCircle className="size-3" aria-hidden="true" />
          {u.state}
        </span>
      );
    },
  },
  {
    id: "last_heartbeat",
    header: "Last heartbeat",
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground whitespace-nowrap">
        {formatRelativeTime(row.original.last_heartbeat)}
      </span>
    ),
  },
  {
    id: "last_sync",
    header: "Last sync",
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground whitespace-nowrap">
        {row.original.last_sync ? formatRelativeTime(row.original.last_sync) : "—"}
      </span>
    ),
  },
  {
    id: "diagnostics",
    header: "Diagnostics",
    cell: ({ row }) => {
      const hasError = Boolean(row.original.last_error);
      const isUnhealthy =
        row.original.health_state === "OFFLINE" || row.original.health_state === "DEGRADED";
      if (!hasError && !isUnhealthy) {
        return <span className="text-xs text-muted-foreground">OK</span>;
      }
      return (
        <span className="inline-flex items-center gap-1 text-xs text-red-400">
          <AlertTriangle className="size-3" aria-hidden="true" />
          {hasError ? "Error" : row.original.health_state}
        </span>
      );
    },
  },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FleetPage() {
  return (
    <Suspense fallback={<LoadingState variant="table" />}>
      <FleetPageContent />
    </Suspense>
  );
}

function FleetPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [lifecycleFilter, setLifecycleFilter] = useState<string>(
    searchParams.get("lifecycle") ?? "all",
  );
  const [healthFilter, setHealthFilter] = useState<string>(searchParams.get("health") ?? "all");
  const [updateFilter, setUpdateFilter] = useState<string>(
    searchParams.get("update") ?? "all",
  );
  const [diagnosticsFilter, setDiagnosticsFilter] = useState<string>(
    searchParams.get("diagnostics") ?? "all",
  );

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (search) params.set("q", search); else params.delete("q");
    if (lifecycleFilter !== "all") params.set("lifecycle", lifecycleFilter); else params.delete("lifecycle");
    if (healthFilter !== "all") params.set("health", healthFilter); else params.delete("health");
    if (updateFilter !== "all") params.set("update", updateFilter); else params.delete("update");
    if (diagnosticsFilter !== "all") params.set("diagnostics", diagnosticsFilter); else params.delete("diagnostics");
    router.replace(`${pathname}?${params.toString()}`);
  }, [search, lifecycleFilter, healthFilter, updateFilter, diagnosticsFilter, pathname, router, searchParams]);

  // Fetch all items (client-side filter mirrors host-page pattern; server
  // query params available if Central supports them).
  const fleet = useFleet({ limit: PAGE_SIZE, offset });

  const filtered = useMemo(() => {
    const items = fleet.data?.items ?? [];
    const query = search.trim().toLowerCase();
    return items.filter((host) => {
      if (query && !host.hostname.toLowerCase().includes(query)) return false;
      if (lifecycleFilter !== "all" && (host.lifecycle_state ?? "ACTIVE") !== lifecycleFilter) return false;
      if (healthFilter !== "all" && host.health_state !== healthFilter) return false;
      if (updateFilter !== "all" && host.update_availability !== (updateFilter as UpdateAvailability)) return false;
      if (diagnosticsFilter === "problems") {
        const hasIssue = Boolean(host.last_error) || host.health_state === "OFFLINE" || host.health_state === "DEGRADED";
        if (!hasIssue) return false;
      }
      return true;
    });
  }, [fleet.data, search, lifecycleFilter, healthFilter, updateFilter, diagnosticsFilter]);

  const resetFilters = () => {
    setSearch("");
    setLifecycleFilter("all");
    setHealthFilter("all");
    setUpdateFilter("all");
    setDiagnosticsFilter("all");
  };

  const outdatedCount = useMemo(
    () => (fleet.data?.items ?? []).filter((h) => h.update_availability === "UPDATE_AVAILABLE").length,
    [fleet.data],
  );

  return (
    <div>
      <PageHeader
        title="Fleet"
        description="Agent version status, health, and upgrade management for all enrolled hosts."
        actions={
          outdatedCount > 0 ? (
            <div className="flex items-center gap-1.5 text-sm text-amber-400">
              <ArrowUpCircle className="size-4" aria-hidden="true" />
              {outdatedCount} host{outdatedCount === 1 ? "" : "s"} with updates available
            </div>
          ) : null
        }
      />

      <FilterBar>
        <SearchInput value={search} onChange={setSearch} placeholder="Search hostname…" className="w-56" />
        <FilterSelect
          label="Lifecycle"
          value={lifecycleFilter}
          onChange={setLifecycleFilter}
          options={[
            { value: "ACTIVE", label: "Active" },
            { value: "DECOMMISSIONED", label: "Decommissioned" },
          ]}
        />
        <FilterSelect
          label="Health"
          value={healthFilter}
          onChange={setHealthFilter}
          options={[
            { value: "HEALTHY", label: "Healthy" },
            { value: "STALE", label: "Stale" },
            { value: "OFFLINE", label: "Offline" },
            { value: "DEGRADED", label: "Degraded" },
          ]}
        />
        <FilterSelect
          label="Updates"
          value={updateFilter}
          onChange={setUpdateFilter}
          options={[
            { value: "UPDATE_AVAILABLE", label: "Update available" },
            { value: "CURRENT", label: "Current" },
            { value: "UNKNOWN", label: "Unknown" },
            { value: "UNSUPPORTED", label: "Unsupported" },
          ]}
        />
        <FilterSelect
          label="Diagnostics"
          value={diagnosticsFilter}
          onChange={setDiagnosticsFilter}
          options={[{ value: "problems", label: "Problems only" }]}
        />
      </FilterBar>

      <ActiveFilters
        filters={[
          ...(search ? [{ label: "Search", value: search, onRemove: () => setSearch("") }] : []),
          ...(lifecycleFilter !== "all" ? [{ label: "Lifecycle", value: lifecycleFilter, onRemove: () => setLifecycleFilter("all") }] : []),
          ...(healthFilter !== "all" ? [{ label: "Health", value: healthFilter, onRemove: () => setHealthFilter("all") }] : []),
          ...(updateFilter !== "all" ? [{ label: "Updates", value: updateFilter, onRemove: () => setUpdateFilter("all") }] : []),
          ...(diagnosticsFilter !== "all" ? [{ label: "Diagnostics", value: diagnosticsFilter, onRemove: () => setDiagnosticsFilter("all") }] : []),
        ]}
        onReset={resetFilters}
      />

      {fleet.isPending ? (
        <LoadingState variant="table" />
      ) : fleet.isError ? (
        <ErrorState error={fleet.error} onRetry={() => void fleet.refetch()} />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={filtered}
            onRowClick={(row) => router.push(`/hosts/${row.id}`)}
            getRowId={(row) => row.id}
          />
          {fleet.data && (
            <PaginationBar
              total={fleet.data.total}
              limit={fleet.data.limit}
              offset={fleet.data.offset}
              onOffsetChange={setOffset}
            />
          )}
        </>
      )}
    </div>
  );
}
