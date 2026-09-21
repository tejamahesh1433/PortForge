"use client";

import Link from "next/link";
import type { ColumnDef } from "@tanstack/react-table";
import React, { useMemo, useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { Lock, Trash2 } from "lucide-react";
import { DataTable } from "@/components/data/data-table";
import { PaginationBar } from "@/components/data/pagination-bar";
import { SearchInput } from "@/components/controls/search-input";
import { FilterBar } from "@/components/controls/filter-bar";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { ConfirmDialog } from "@/components/controls/confirm-dialog";
import { useHosts } from "@/hooks/use-hosts";
import { useReservations, useDeleteDashboardReservation } from "@/hooks/use-reservations";
import { formatAbsoluteTime } from "@/lib/utils/format";
import type { ReservationOut } from "@/lib/types/api";
import { ReservationModal } from "@/components/forms/reservation-modal";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";

const PAGE_SIZE = 100;

export default function ReservationsPage() {
  return (
    <Suspense fallback={<LoadingState variant="table" rows={8} />}>
      <ReservationsPageContent />
    </Suspense>
  );
}

function ReservationsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const initialProject = searchParams.get("project") ?? "";

  const [offset, setOffset] = useState(0);
  const [project, setProject] = useState(initialProject);

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (project) {
      params.set("project", project);
    } else {
      params.delete("project");
    }
    router.replace(`${pathname}?${params.toString()}`);
  }, [project, pathname, router, searchParams]);

  const reservations = useReservations({ limit: PAGE_SIZE, offset, project: project || undefined });
  // Reservations carry only host_id (see backend/app/schemas/reservation.py);
  // the hostname shown here is a client-side join against the real hosts
  // list, not an invented field.
  const hosts = useHosts({ limit: 500 });
  const deleteMutation = useDeleteDashboardReservation();

  const hostnameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const host of hosts.data?.items ?? []) map.set(host.id, host.hostname);
    return map;
  }, [hosts.data]);

  const [pendingRelease, setPendingRelease] = useState<ReservationOut | null>(null);

  const handleDelete = React.useCallback((reservation: ReservationOut) => {
    setPendingRelease(reservation);
  }, []);

  const confirmRelease = React.useCallback(() => {
    if (!pendingRelease) return;
    deleteMutation.mutate(
      { hostId: pendingRelease.host_id, reservationId: pendingRelease.id },
      {
        onSuccess: () => {
          toast.add({
            type: "success",
            title: "Reservation released",
            description: `Port ${pendingRelease.port} is no longer reserved.`,
          });
          setPendingRelease(null);
        },
        onError: (error) => {
          toast.add({
            type: "error",
            title: "Failed to release reservation",
            description: error instanceof Error ? error.message : "Unknown error occurred.",
          });
        },
      },
    );
  }, [deleteMutation, pendingRelease]);

  const columns = useMemo<ColumnDef<ReservationOut, unknown>[]>(
    () => [
      {
        id: "host",
        header: "Host",
        cell: ({ row }) => {
          const hostname = hostnameById.get(row.original.host_id);
          return (
            <Link
              href={`/hosts/${row.original.host_id}`}
              className="text-sm text-foreground underline-offset-2 hover:text-primary hover:underline"
            >
              {hostname ?? row.original.host_id.slice(0, 8)}
            </Link>
          );
        },
      },
      {
        accessorKey: "port",
        header: "Port",
        cell: ({ row }) => <span className="font-mono text-sm font-medium">{row.original.port}</span>,
      },
      {
        accessorKey: "protocol",
        header: "Protocol",
        cell: ({ row }) => <span className="text-xs uppercase text-muted-foreground">{row.original.protocol}</span>,
      },
      {
        accessorKey: "bind_address",
        header: "Bind Address",
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.bind_address ?? "0.0.0.0"}</span>
        ),
      },
      { accessorKey: "project", header: "Project" },
      {
        accessorKey: "service",
        header: "Service",
        cell: ({ row }) => row.original.service ?? <span className="text-muted-foreground">—</span>,
      },
      {
        accessorKey: "purpose",
        header: "Purpose",
        cell: ({ row }) => row.original.purpose ?? <span className="text-muted-foreground">—</span>,
      },
      {
        accessorKey: "updated_at",
        header: "Updated",
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">{formatAbsoluteTime(row.original.updated_at)}</span>
        ),
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <Button 
            variant="ghost" 
            size="icon" 
            className="h-8 w-8 text-muted-foreground hover:text-destructive"
            onClick={(e) => {
              e.stopPropagation();
              handleDelete(row.original);
            }}
            title="Release reservation"
          >
            <Trash2 className="size-4" />
          </Button>
        ),
      },
    ],
    [hostnameById, handleDelete],
  );

  return (
    <div>
      <div className="flex items-start justify-between">
        <PageHeader 
          title="Reservations" 
          description="Ports claimed for a project, synchronized from each agent."
        />
        <div className="mt-2">
          <ReservationModal />
        </div>
      </div>

      <FilterBar>
        <SearchInput value={project} onChange={setProject} placeholder="Filter by project…" className="w-64" />
      </FilterBar>

      {reservations.isPending ? (
        <LoadingState variant="table" rows={8} />
      ) : reservations.isError ? (
        <ErrorState error={reservations.error} onRetry={() => void reservations.refetch()} />
      ) : (reservations.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={Lock}
          title="No reservations"
          description="No project has reserved a port with Central yet."
        />
      ) : (
        <>
          <DataTable columns={columns} data={reservations.data!.items} getRowId={(row) => row.id} />
          {reservations.data && (
            <PaginationBar
              total={reservations.data.total}
              limit={reservations.data.limit}
              offset={reservations.data.offset}
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
        title="Release reservation?"
        description={
          pendingRelease
            ? `This releases port ${pendingRelease.port}/${pendingRelease.protocol} on ${hostnameById.get(pendingRelease.host_id) ?? "this host"}. The project will need to reserve it again to reclaim it.`
            : ""
        }
        confirmLabel="Release"
        destructive
        onConfirm={confirmRelease}
        isConfirming={deleteMutation.isPending}
      />
    </div>
  );
}
