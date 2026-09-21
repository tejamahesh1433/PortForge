"use client";

import Link from "next/link";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { Box, Container } from "lucide-react";
import { PortSourceBadge } from "@/components/status/port-source-badge";
import { PortStateBadge } from "@/components/status/port-state-badge";
import { EmptyState } from "@/components/feedback/empty-state";
import type { PortObservationOut } from "@/lib/types/api";
import { DataTable } from "./data-table";
import { PortInspector } from "./port-inspector";

interface PortTableProps {
  ports: PortObservationOut[];
  /** Show the "Host" column -- omitted on a single host's own detail
   * page, where every row is already that host by definition.
   */
  showHost?: boolean;
}

function OwnerCell({ port }: { port: PortObservationOut }) {
  if (port.source === "docker" && port.container_name) {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm">
        <Container className="size-3.5 shrink-0 text-sky-400" aria-hidden="true" />
        <span className="truncate max-w-[200px]" title={port.container_name}>{port.container_name}</span>
      </span>
    );
  }
  if (port.process_name) {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm">
        <Box className="size-3.5 shrink-0 text-amber-400" aria-hidden="true" />
        <span className="truncate max-w-[200px]" title={port.process_name}>
          {port.process_name}
          {port.pid ? <span className="text-muted-foreground"> ({port.pid})</span> : null}
        </span>
      </span>
    );
  }
  return <span className="text-muted-foreground">—</span>;
}

/** The real physical-binding table (Port/Protocol/Bind Address/State/
 * Source/Owner/Project/Purpose), built on the DataTable primitive.
 * Columns are based directly on PortObservationOut's real fields -- no
 * invented columns.
 */
export function PortTable({ ports, showHost = true }: PortTableProps) {
  const [selectedPort, setSelectedPort] = useState<PortObservationOut | null>(null);

  const columns = useMemo<ColumnDef<PortObservationOut, unknown>[]>(() => {
    const base: ColumnDef<PortObservationOut, unknown>[] = [
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
    ];

    if (showHost) {
      base.push({
        id: "host",
        accessorFn: (row) => row.host_hostname ?? "",
        header: "Host",
        cell: ({ row }) =>
          row.original.host_hostname ? (
            <Link
              href={`/hosts/${row.original.host_id}`}
              className="text-sm text-foreground underline-offset-2 hover:text-primary hover:underline block truncate max-w-[150px]"
              title={row.original.host_hostname}
              onClick={(event) => event.stopPropagation()}
            >
              {row.original.host_hostname}
            </Link>
          ) : (
            <span className="text-muted-foreground">—</span>
          ),
      });
    }

    base.push(
      {
        accessorKey: "bind_address",
        header: "Bind Address",
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.bind_address}</span>,
      },
      {
        accessorKey: "state",
        header: "State",
        cell: ({ row }) => <PortStateBadge state={row.original.state} />,
      },
      {
        accessorKey: "source",
        header: "Source",
        cell: ({ row }) => <PortSourceBadge source={row.original.source} />,
      },
      {
        id: "owner",
        header: "Process / Container",
        cell: ({ row }) => <OwnerCell port={row.original} />,
      },
      {
        accessorKey: "project_name",
        header: "Project",
        cell: ({ row }) => (
          <span className="text-sm block truncate max-w-[120px]" title={row.original.project_name ?? ""}>
            {row.original.project_name ?? <span className="text-muted-foreground">—</span>}
          </span>
        ),
      },
      {
        accessorKey: "purpose",
        header: "Purpose",
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground block truncate max-w-[120px]" title={row.original.purpose ?? row.original.category ?? ""}>
            {row.original.purpose ?? row.original.category ?? "—"}
          </span>
        ),
      },
    );

    return base;
  }, [showHost]);

  if (ports.length === 0) {
    return (
      <EmptyState
        title="No port bindings"
        description="No current observations match this view yet."
      />
    );
  }

  return (
    <>
      <DataTable 
        columns={columns} 
        data={ports} 
        getRowId={(row) => row.id} 
        onRowClick={(row) => setSelectedPort(row)}
      />
      <PortInspector 
        port={selectedPort} 
        open={selectedPort !== null} 
        onOpenChange={(open) => !open && setSelectedPort(null)} 
      />
    </>
  );
}
