"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { Suspense, useState, useEffect } from "react";
import { PaginationBar } from "@/components/data/pagination-bar";
import { PortTable } from "@/components/data/port-table";
import { FilterBar } from "@/components/controls/filter-bar";
import { ActiveFilters } from "@/components/controls/active-filters";
import { FilterSelect } from "@/components/controls/filter-select";
import { SearchInput } from "@/components/controls/search-input";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { usePorts } from "@/hooks/use-ports";

const PAGE_SIZE = 100;

/** The global Ports page interprets the TopBar's free-text search as a
 * `project` filter when Central's `/api/ports` query params support it
 * directly (project is a case-insensitive substring match server-side --
 * see backend/app/repositories/port_repository.py:query); a purely
 * numeric query is instead sent as an exact `port` filter. This keeps
 * filtering server-side (so pagination/totals stay correct) rather than
 * re-implementing substring search over a client-side page of results.
 */
export default function PortsPage() {
  return (
    <Suspense fallback={<LoadingState variant="table" rows={10} />}>
      <PortsPageContent />
    </Suspense>
  );
}

function PortsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const initialQuery = searchParams.get("q") ?? searchParams.get("port") ?? searchParams.get("project") ?? "";
  const initialSource = searchParams.get("source") ?? "all";

  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(initialQuery);
  const [sourceFilter, setSourceFilter] = useState(initialSource);

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (search) {
      if (/^\d+$/.test(search.trim())) {
        params.set("port", search.trim());
        params.delete("q");
        params.delete("project");
      } else {
        params.set("q", search.trim());
        params.delete("port");
        params.delete("project");
      }
    } else {
      params.delete("q");
      params.delete("port");
      params.delete("project");
    }

    if (sourceFilter !== "all") {
      params.set("source", sourceFilter);
    } else {
      params.delete("source");
    }

    router.replace(`${pathname}?${params.toString()}`);
  }, [search, sourceFilter, pathname, router, searchParams]);

  const handleSearchChange = (value: string) => {
    setSearch(value);
    setOffset(0);
  };
  const handleSourceFilterChange = (value: string) => {
    setSourceFilter(value);
    setOffset(0);
  };

  const isNumeric = /^\d+$/.test(search.trim());

  const ports = usePorts({
    limit: PAGE_SIZE,
    offset,
    port: isNumeric ? Number(search.trim()) : undefined,
    project: !isNumeric && search.trim() ? search.trim() : undefined,
    source: sourceFilter === "all" ? undefined : sourceFilter,
  });

  return (
    <div>
      <PageHeader title="Ports" description="Every current port binding Central has observed, across every host." />

      <FilterBar>
        <SearchInput
          value={search}
          onChange={handleSearchChange}
          placeholder="Search by port number or project…"
          className="w-72"
        />
        <FilterSelect
          label="source"
          value={sourceFilter}
          onChange={handleSourceFilterChange}
          options={[
            { value: "process", label: "Process" },
            { value: "docker", label: "Docker" },
            { value: "system", label: "System" },
          ]}
        />
      </FilterBar>
      <ActiveFilters
        filters={[
          ...(search ? [{ label: isNumeric ? "Port" : "Project", value: search, onRemove: () => handleSearchChange("") }] : []),
          ...(sourceFilter !== "all" ? [{ label: "Source", value: sourceFilter, onRemove: () => handleSourceFilterChange("all") }] : []),
        ]}
        onReset={() => { handleSearchChange(""); handleSourceFilterChange("all"); }}
      />

      {ports.isPending ? (
        <LoadingState variant="table" rows={10} />
      ) : ports.isError ? (
        <ErrorState error={ports.error} onRetry={() => void ports.refetch()} />
      ) : (
        <>
          <PortTable ports={ports.data?.items ?? []} />
          {ports.data && (
            <PaginationBar
              total={ports.data.total}
              limit={ports.data.limit}
              offset={ports.data.offset}
              onOffsetChange={setOffset}
            />
          )}
        </>
      )}
    </div>
  );
}


