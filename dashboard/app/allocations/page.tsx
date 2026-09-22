"use client";

import Link from "next/link";
import type { ColumnDef } from "@tanstack/react-table";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { Layers, Trash2 } from "lucide-react";
import { DataTable } from "@/components/data/data-table";
import { PaginationBar } from "@/components/data/pagination-bar";
import { SearchInput } from "@/components/controls/search-input";
import { FilterBar } from "@/components/controls/filter-bar";
import { FilterSelect } from "@/components/controls/filter-select";
import { ActiveFilters } from "@/components/controls/active-filters";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { ConfirmDialog } from "@/components/controls/confirm-dialog";
import { StatusBadge } from "@/components/status/status-badge";
import { BindProbeBadge } from "@/components/status/bind-probe-badge";
import { Button } from "@/components/ui/button";
import { useHosts } from "@/hooks/use-hosts";
import { useAllocations, useReleaseAllocation } from "@/hooks/use-allocations";
import { formatAbsoluteTime } from "@/lib/utils/format";
import type { AllocationOut, BindProbeEvidence } from "@/lib/types/api";
import { toast } from "@/components/ui/toast";
import { CreateAllocationDialog } from "@/components/allocations/create-allocation-dialog";

const PAGE_SIZE = 50;

/** Strongest evidence across a bundle's entries, for the list's compact
 * probe column -- mirrors the same "strongest wins" reasoning already used
 * server-side for AllocationValidationOut.bind_probe (v1.1-B). */
const PROBE_STRENGTH: Record<BindProbeEvidence, number> = {
  verified_free: 3,
  verified_occupied: 2,
  expired: 1,
  unavailable: 1,
  not_remote_capable: 0,
};

function strongestBindProbe(allocation: AllocationOut): BindProbeEvidence | null {
  if (allocation.allocations.length === 0) return null;
  return allocation.allocations.reduce<BindProbeEvidence>(
    (best, entry) => (PROBE_STRENGTH[entry.bind_probe] > PROBE_STRENGTH[best] ? entry.bind_probe : best),
    allocation.allocations[0].bind_probe,
  );
}

export default function AllocationsPage() {
  return (
    <Suspense fallback={<LoadingState variant="table" rows={8} />}>
      <AllocationsPageContent />
    </Suspense>
  );
}

function AllocationsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [hostId, setHostId] = useState(searchParams.get("host_id") ?? "all");
  const [status, setStatus] = useState(searchParams.get("status") ?? "all");

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (search) params.set("q", search);
    else params.delete("q");
    if (hostId !== "all") params.set("host_id", hostId);
    else params.delete("host_id");
    if (status !== "all") params.set("status", status);
    else params.delete("status");
    router.replace(`${pathname}?${params.toString()}`);
  }, [search, hostId, status, pathname, router, searchParams]);

  const handleSearchChange = useCallback((value: string) => {
    setSearch(value);
    setOffset(0);
  }, []);
  const handleHostChange = useCallback((value: string) => {
    setHostId(value);
    setOffset(0);
  }, []);
  const handleStatusChange = useCallback((value: string) => {
    setStatus(value);
    setOffset(0);
  }, []);

  const hosts = useHosts({ limit: 500 });
  const allocations = useAllocations({
    limit: PAGE_SIZE,
    offset,
    search: search || undefined,
    host_id: hostId === "all" ? undefined : hostId,
    status: status === "all" ? undefined : (status as "active" | "released"),
  });
  const releaseMutation = useReleaseAllocation();

  const hostnameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const host of hosts.data?.items ?? []) map.set(host.id, host.hostname);
    return map;
  }, [hosts.data]);

  const [pendingRelease, setPendingRelease] = useState<AllocationOut | null>(null);

  const confirmRelease = useCallback(() => {
    if (!pendingRelease) return;
    releaseMutation.mutate(pendingRelease.allocation_id, {
      onSuccess: () => {
        toast.add({
          type: "success",
          title: "Allocation released",
          description: `${pendingRelease.allocations.length} binding${pendingRelease.allocations.length === 1 ? "" : "s"} for '${pendingRelease.project}' released.`,
        });
        setPendingRelease(null);
      },
      onError: (error) => {
        toast.add({
          type: "error",
          title: "Failed to release allocation",
          description: error instanceof Error ? error.message : "Unknown error occurred.",
        });
      },
    });
  }, [releaseMutation, pendingRelease]);

  const columns = useMemo<ColumnDef<AllocationOut, unknown>[]>(
    () => [
      {
        accessorKey: "project",
        header: "Project",
        cell: ({ row }) => <span className="font-medium text-foreground">{row.original.project}</span>,
      },
      {
        id: "host",
        header: "Host",
        cell: ({ row }) => (
          <Link
            href={`/hosts/${row.original.host.id}`}
            onClick={(e) => e.stopPropagation()}
            className="text-sm text-foreground underline-offset-2 hover:text-primary hover:underline"
          >
            {hostnameById.get(row.original.host.id) ?? row.original.host.hostname}
          </Link>
        ),
      },
      {
        id: "bindings",
        header: "Bindings",
        cell: ({ row }) => {
          const entries = row.original.allocations;
          if (entries.length === 0) {
            return <span className="text-xs text-muted-foreground">no live bindings</span>;
          }
          return (
            <div className="flex flex-wrap gap-1">
              {entries.slice(0, 4).map((entry) => (
                <span
                  key={entry.reservation_id}
                  className="rounded-md border border-border bg-muted/40 px-1.5 py-0.5 font-mono text-xs"
                  title={`${entry.name} (${entry.purpose}) -- ${entry.protocol}/${entry.port}`}
                >
                  {entry.port}
                </span>
              ))}
              {entries.length > 4 && (
                <span className="text-xs text-muted-foreground">+{entries.length - 4} more</span>
              )}
            </div>
          );
        },
      },
      {
        id: "probe",
        header: "Probe evidence",
        cell: ({ row }) => {
          const strongest = strongestBindProbe(row.original);
          return strongest ? <BindProbeBadge value={strongest} /> : <span className="text-muted-foreground text-xs">—</span>;
        },
      },
      {
        accessorKey: "status",
        header: "State",
        cell: ({ row }) => <StatusBadge value={row.original.status} />,
      },
      {
        id: "request_id",
        header: "Request ID",
        cell: ({ row }) =>
          row.original.request_id ? (
            <span className="font-mono text-xs text-muted-foreground">{row.original.request_id}</span>
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          ),
      },
      {
        accessorKey: "created_at",
        header: "Created",
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">{formatAbsoluteTime(row.original.created_at)}</span>
        ),
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) =>
          row.original.status === "active" ? (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                setPendingRelease(row.original);
              }}
              title="Release allocation"
            >
              <Trash2 className="size-4" />
            </Button>
          ) : null,
      },
    ],
    [hostnameById],
  );

  return (
    <div>
      <PageHeader
        title="Allocations"
        description="Atomic, multi-port bundles requested by coding agents and workflows (Phase 8A)."
        actions={<CreateAllocationDialog hosts={hosts.data?.items ?? []} onCreated={() => void allocations.refetch()} />}
      />

      <FilterBar>
        <SearchInput
          value={search}
          onChange={handleSearchChange}
          placeholder="Search by project or request ID…"
          className="w-72"
        />
        <FilterSelect
          label="host"
          value={hostId}
          onChange={handleHostChange}
          allValue="all"
          options={(hosts.data?.items ?? []).map((host) => ({ value: host.id, label: host.hostname }))}
        />
        <FilterSelect
          label="state"
          value={status}
          onChange={handleStatusChange}
          allValue="all"
          options={[
            { value: "active", label: "Active" },
            { value: "released", label: "Released" },
          ]}
        />
      </FilterBar>
      <ActiveFilters
        filters={[
          ...(search ? [{ label: "Search", value: search, onRemove: () => handleSearchChange("") }] : []),
          ...(hostId !== "all"
            ? [{ label: "Host", value: hostnameById.get(hostId) ?? hostId, onRemove: () => handleHostChange("all") }]
            : []),
          ...(status !== "all" ? [{ label: "State", value: status, onRemove: () => handleStatusChange("all") }] : []),
        ]}
        onReset={() => {
          handleSearchChange("");
          handleHostChange("all");
          handleStatusChange("all");
        }}
      />

      {allocations.isPending ? (
        <LoadingState variant="table" rows={8} />
      ) : allocations.isError ? (
        <ErrorState error={allocations.error} onRetry={() => void allocations.refetch()} />
      ) : (allocations.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={Layers}
          title="No allocations"
          description="No coding agent or workflow has allocated a port bundle yet."
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={allocations.data!.items}
            getRowId={(row) => row.allocation_id}
            onRowClick={(row) => router.push(`/allocations/${row.allocation_id}`)}
          />
          {allocations.data && (
            <PaginationBar
              total={allocations.data.total}
              limit={allocations.data.limit}
              offset={allocations.data.offset}
              onOffsetChange={setOffset}
            />
          )}
        </>
      )}

      <ConfirmDialog
        open={pendingRelease !== null}
        onOpenChange={(open) => {
          if (!open) setPendingRelease(null);
        }}
        title="Release allocation?"
        description={
          pendingRelease
            ? `This releases all ${pendingRelease.allocations.length} binding${pendingRelease.allocations.length === 1 ? "" : "s"} in this bundle for '${pendingRelease.project}' on ${hostnameById.get(pendingRelease.host.id) ?? pendingRelease.host.hostname}. This cannot be undone from here.`
            : ""
        }
        confirmLabel="Release"
        destructive
        onConfirm={confirmRelease}
        isConfirming={releaseMutation.isPending}
      />
    </div>
  );
}
