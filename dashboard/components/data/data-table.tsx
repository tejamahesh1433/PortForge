"use client";

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "cn";

interface DataTableProps<TData> {
  columns: ColumnDef<TData, unknown>[];
  data: TData[];
  /** Called on row click/Enter, if rows should be navigable. */
  onRowClick?: (row: TData) => void;
  getRowId?: (row: TData) => string;
}

/**
 * Generic sortable table primitive built on TanStack Table + shadcn's
 * Table parts. Every real data table in the app (PortTable, and any
 * future Reservations/Conflicts tables) composes this rather than
 * hand-rolling its own <table> markup, so sorting/keyboard/row-click
 * behavior stays consistent. Pagination is handled by the caller (server
 * -side, via the Page<T> envelope every list endpoint returns) rather
 * than by this component, since "pagination if the API supports it" (it
 * does, uniformly) belongs at the query layer, not duplicated per table.
 */
export function DataTable<TData>({ columns, data, onRowClick, getRowId }: DataTableProps<TData>) {
  const [sorting, setSorting] = useState<SortingState>([]);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId,
  });

  return (
    <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-250px)] rounded-lg border border-border">
      <Table className="relative">
        <TableHeader className="sticky top-0 z-10 bg-background/95 backdrop-blur shadow-sm">
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent">
              {headerGroup.headers.map((header) => {
                const sortable = header.column.getCanSort();
                const sortDirection = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    className={cn("whitespace-nowrap", sortable && "cursor-pointer select-none")}
                    onClick={sortable ? header.column.getToggleSortingHandler() : undefined}
                    aria-sort={
                      sortDirection === "asc" ? "ascending" : sortDirection === "desc" ? "descending" : "none"
                    }
                  >
                    {header.isPlaceholder ? null : (
                      <span className="inline-flex items-center gap-1">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {sortable &&
                          (sortDirection === "asc" ? (
                            <ArrowUp className="size-3.5" aria-hidden="true" />
                          ) : sortDirection === "desc" ? (
                            <ArrowDown className="size-3.5" aria-hidden="true" />
                          ) : (
                            <ArrowUpDown className="size-3.5 opacity-40" aria-hidden="true" />
                          ))}
                      </span>
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                No rows.
              </TableCell>
            </TableRow>
          ) : (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                data-state={undefined}
                tabIndex={onRowClick ? 0 : undefined}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onRowClick(row.original);
                        }
                      }
                    : undefined
                }
                className={cn(
                  onRowClick && "cursor-pointer outline-none focus-visible:bg-muted/60 hover:bg-muted/40",
                )}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className="py-2">{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
