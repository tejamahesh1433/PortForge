"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { HostGrid } from "@/components/data/host-grid";
import { PaginationBar } from "@/components/data/pagination-bar";
import { FilterBar } from "@/components/controls/filter-bar";
import { ActiveFilters } from "@/components/controls/active-filters";
import { FilterSelect } from "@/components/controls/filter-select";
import { SearchInput } from "@/components/controls/search-input";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { useHosts } from "@/hooks/use-hosts";
import { getHostHealthState } from "@/lib/utils/host-health";

const PAGE_SIZE = 50;

export default function HostsPage() {
  return (
    <Suspense fallback={<LoadingState variant="cards" rows={6} />}>
      <HostsPageContent />
    </Suspense>
  );
}

function HostsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [osFilter, setOsFilter] = useState(searchParams.get("os") ?? "all");
  const [statusFilter, setStatusFilter] = useState(searchParams.get("status") ?? "all");
  const [dockerFilter, setDockerFilter] = useState(searchParams.get("docker") ?? "all");

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (search) params.set("q", search); else params.delete("q");
    if (osFilter !== "all") params.set("os", osFilter); else params.delete("os");
    if (statusFilter !== "all") params.set("status", statusFilter); else params.delete("status");
    if (dockerFilter !== "all") params.set("docker", dockerFilter); else params.delete("docker");
    router.replace(`${pathname}?${params.toString()}`);
  }, [search, osFilter, statusFilter, dockerFilter, pathname, router, searchParams]);

  const hosts = useHosts({ limit: PAGE_SIZE, offset });

  const osOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const host of hosts.data?.items ?? []) seen.add(host.operating_system);
    return Array.from(seen).map((value) => ({ value, label: value }));
  }, [hosts.data]);

  const filtered = useMemo(() => {
    const items = hosts.data?.items ?? [];
    const query = search.trim().toLowerCase();
    return items.filter((host) => {
      if (query && !host.hostname.toLowerCase().includes(query)) return false;
      if (osFilter !== "all" && host.operating_system !== osFilter) return false;
      if (statusFilter !== "all" && getHostHealthState(host).toLowerCase() !== statusFilter) return false;
      if (dockerFilter === "available" && !host.docker_available) return false;
      if (dockerFilter === "unavailable" && host.docker_available) return false;
      return true;
    });
  }, [hosts.data, search, osFilter, statusFilter, dockerFilter]);

  const resetFilters = () => {
    setSearch("");
    setOsFilter("all");
    setStatusFilter("all");
    setDockerFilter("all");
  };

  return (
    <div>
      <PageHeader title="Hosts" description="Every machine currently enrolled with Central." />

      <FilterBar>
        <SearchInput value={search} onChange={setSearch} placeholder="Search hostname…" className="w-56" />
        <FilterSelect label="OS" value={osFilter} onChange={setOsFilter} options={osOptions} />
        <FilterSelect
          label="status"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "healthy", label: "Healthy" },
            { value: "stale", label: "Stale" },
            { value: "offline", label: "Offline" },
          ]}
        />
        <FilterSelect
          label="Docker"
          value={dockerFilter}
          onChange={setDockerFilter}
          options={[
            { value: "available", label: "Available" },
            { value: "unavailable", label: "Unavailable" },
          ]}
        />
      </FilterBar>
      <ActiveFilters
        filters={[
          ...(search ? [{ label: "Search", value: search, onRemove: () => setSearch("") }] : []),
          ...(osFilter !== "all" ? [{ label: "OS", value: osFilter, onRemove: () => setOsFilter("all") }] : []),
          ...(statusFilter !== "all" ? [{ label: "Status", value: statusFilter, onRemove: () => setStatusFilter("all") }] : []),
          ...(dockerFilter !== "all" ? [{ label: "Docker", value: dockerFilter, onRemove: () => setDockerFilter("all") }] : []),
        ]}
        onReset={resetFilters}
      />

      {hosts.isPending ? (
        <LoadingState variant="cards" rows={6} />
      ) : hosts.isError ? (
        <ErrorState error={hosts.error} onRetry={() => void hosts.refetch()} />
      ) : (
        <>
          <HostGrid hosts={filtered} filtered={search !== "" || osFilter !== "all" || statusFilter !== "all" || dockerFilter !== "all"} />
          {hosts.data && (
            <PaginationBar
              total={hosts.data.total}
              limit={hosts.data.limit}
              offset={hosts.data.offset}
              onOffsetChange={setOffset}
            />
          )}
        </>
      )}
    </div>
  );
}
