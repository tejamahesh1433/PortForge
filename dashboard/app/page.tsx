"use client";

import Link from "next/link";
import { useMemo } from "react";
import { AlertTriangle, ArrowUpRight, Folder, Layers, Lock, Network, Server } from "lucide-react";
import { HostGrid } from "@/components/data/host-grid";
import { MetricCard } from "@/components/data/metric-card";
import { ActivityFeed } from "@/components/activity/activity-feed";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { CheckFleetAgainButton } from "@/components/hosts/check-again-button";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { useAllocations } from "@/hooks/use-allocations";
import { useConflicts } from "@/hooks/use-conflicts";
import { useHealth } from "@/hooks/use-health";
import { useHosts } from "@/hooks/use-hosts";
import { usePorts } from "@/hooks/use-ports";
import { useProjects } from "@/hooks/use-projects";
import { useReservations } from "@/hooks/use-reservations";
import { getHostHealthState } from "@/lib/utils/host-health";

const OVERVIEW_HOST_LIMIT = 100;
const OVERVIEW_PORT_LIMIT = 500;

export default function OverviewPage() {
  const hosts = useHosts({ limit: OVERVIEW_HOST_LIMIT });
  const ports = usePorts({ limit: OVERVIEW_PORT_LIMIT });
  const reservations = useReservations({ limit: 1 });
  const activeAllocations = useAllocations({ status: "active", limit: 1 });
  const projects = useProjects();
  const conflicts = useConflicts();
  const health = useHealth();

  const isLoading =
    hosts.isPending ||
    ports.isPending ||
    projects.isPending ||
    reservations.isPending ||
    conflicts.isPending ||
    activeAllocations.isPending;
  const firstError =
    hosts.error ?? ports.error ?? projects.error ?? reservations.error ?? conflicts.error ?? activeAllocations.error;

  const bindingData = useMemo(() => {
    if (!ports.data?.items || !hosts.data?.items) return [];
    const counts = new Map<string, { hostId: string; name: string; count: number }>();
    for (const port of ports.data.items) {
      if (port.host_hostname) {
        const current = counts.get(port.host_id);
        counts.set(port.host_id, {
          hostId: port.host_id,
          name: port.host_hostname,
          count: (current?.count ?? 0) + 1,
        });
      }
    }
    return Array.from(counts.values()).sort((a, b) => b.count - a.count);
  }, [ports.data, hosts.data]);

  const portStats = useMemo(() => {
    if (!ports.data?.items) return { docker: 0, process: 0, system: 0 };
    let docker = 0, process = 0, system = 0;
    for (const p of ports.data.items) {
      if (p.source === "docker") docker++;
      else if (p.source === "process") process++;
      else if (p.source === "system") system++;
    }
    return { docker, process, system };
  }, [ports.data]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <PageHeader title="Operations Overview" description="Fleet-wide summary across every enrolled host." />
        <LoadingState variant="cards" rows={4} />
      </div>
    );
  }

  if (firstError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Operations Overview" description="Fleet-wide summary across every enrolled host." />
        <ErrorState
          error={firstError}
          onRetry={() => {
            void hosts.refetch();
            void ports.refetch();
            void projects.refetch();
            void reservations.refetch();
            void conflicts.refetch();
            void activeAllocations.refetch();
          }}
        />
      </div>
    );
  }

  const hostItems = hosts.data?.items ?? [];
  const healthyCount = hostItems.filter((host) => getHostHealthState(host) === "HEALTHY").length;
  const staleCount = hostItems.filter((host) => getHostHealthState(host) === "STALE").length;
  const offlineCount = hostItems.filter((host) => getHostHealthState(host) === "OFFLINE").length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Operations Overview"
        description={
          health.data
            ? `Central v${health.data.version} — database ${health.data.database}.`
            : "Fleet-wide summary across every enrolled host."
        }
        actions={staleCount + offlineCount > 0 ? <CheckFleetAgainButton /> : null}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-6">
        <MetricCard
          label="Hosts"
          value={hosts.data?.total ?? 0}
          icon={Server}
          detail={`${healthyCount} healthy · ${staleCount} stale · ${offlineCount} offline`}
          accent={offlineCount > 0 ? "red" : staleCount > 0 ? "amber" : "blue"}
          href="/hosts"
        />
        <MetricCard
          label="Port bindings"
          value={ports.data?.total ?? 0}
          icon={Network}
          accent="default"
          detail={`${portStats.docker} docker · ${portStats.process} process · ${portStats.system} sys`}
          href="/ports"
        />
        <MetricCard label="Projects" value={projects.data?.length ?? 0} icon={Folder} href="/projects" />
        <MetricCard
          label="Reservations"
          value={reservations.data?.total ?? 0}
          icon={Lock}
          accent="violet"
          href="/reservations"
        />
        <MetricCard
          label="Active allocations"
          value={activeAllocations.data?.total ?? 0}
          icon={Layers}
          accent="blue"
          detail="Atomic port bundles"
          href="/allocations"
        />
        <MetricCard
          label="Conflicts"
          value={conflicts.data?.length ?? 0}
          icon={AlertTriangle}
          accent={conflicts.data && conflicts.data.length > 0 ? "red" : "emerald"}
          detail={conflicts.data?.length === 0 ? "No active conflicts" : undefined}
          href="/conflicts"
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-foreground">Fleet Inventory</h3>
            {hosts.data && hosts.data.total > OVERVIEW_HOST_LIMIT && (
              <Link href="/hosts" className="text-xs text-primary hover:underline">
                View all {hosts.data.total} hosts →
              </Link>
            )}
          </div>
          <HostGrid hosts={hostItems} />
        </div>
        
        {bindingData.length > 0 && (
          <Card className="self-start border-border bg-card">
            <CardHeader className="border-b border-border/70 pb-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-foreground">Bindings by host</p>
                  <p className="mt-1 text-xs text-muted-foreground">Live port footprint across the fleet</p>
                </div>
                <div className="rounded-lg bg-primary/10 px-2.5 py-1.5 text-right">
                  <p className="text-lg font-semibold leading-none text-primary">{ports.data?.total ?? 0}</p>
                  <p className="mt-1 text-[9px] font-semibold uppercase tracking-[0.16em] text-primary/70">total</p>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-1 px-2">
              {bindingData.slice(0, 6).map((host, index) => {
                const maxBindings = bindingData[0]?.count ?? 1;
                const share = Math.max((host.count / maxBindings) * 100, 8);

                return (
                  <Link
                    key={host.hostId}
                    href={`/hosts/${host.hostId}`}
                    className="group flex items-center gap-3 rounded-lg px-2 py-2.5 transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="w-5 text-right font-mono text-[10px] text-muted-foreground/60">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="mb-1.5 flex items-center justify-between gap-3">
                        <span className="truncate text-xs font-medium text-foreground">{host.name}</span>
                        <span className="shrink-0 font-mono text-xs font-semibold text-foreground">{host.count}</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary transition-[width] duration-500 group-hover:bg-primary/80"
                          style={{ width: `${share}%` }}
                        />
                      </div>
                    </div>
                    <ArrowUpRight className="size-3.5 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-primary" aria-hidden="true" />
                  </Link>
                );
              })}
              {bindingData.length > 6 && (
                <Link href="/ports" className="flex items-center justify-center gap-1 py-2 text-xs font-medium text-primary hover:underline">
                  View {bindingData.length - 6} more hosts <ArrowUpRight className="size-3" aria-hidden="true" />
                </Link>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      <div className="space-y-4 pt-4 border-t border-border">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">Recent Activity</h3>
          <Link href="/activity" className="text-xs text-primary hover:underline">
            View all activity →
          </Link>
        </div>
        <div className="max-w-4xl">
          <ActivityFeed limit={10} compact />
        </div>
      </div>
    </div>
  );
}




