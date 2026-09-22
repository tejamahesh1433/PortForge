"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";
import { Cpu, Folder, HardDrive, Server, Terminal, Activity } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ActivityFeed } from "@/components/activity/activity-feed";
import { Card, CardContent } from "@/components/ui/card";
import { PortTable } from "@/components/data/port-table";
import { DockerStatus } from "@/components/status/docker-status";
import { HostStatus } from "@/components/status/host-status";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { EmptyState } from "@/components/feedback/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { useHost, useHostPorts, useHostDiagnostics } from "@/hooks/use-hosts";
import { formatAbsoluteTime } from "@/lib/utils/format";
import { FreshnessWarning } from "@/components/status/freshness-warning";
import { CompatibilityCard, ProbeCapabilityCard } from "@/components/status/host-compatibility-card";

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}

export default function HostDetailPage() {
  const params = useParams<{ hostId: string }>();
  const hostId = params.hostId;

  const host = useHost(hostId);
  const ports = useHostPorts(hostId);

  const dockerPorts = useMemo(() => (ports.data ?? []).filter((p) => p.source === "docker"), [ports.data]);
  const processPorts = useMemo(
    () => (ports.data ?? []).filter((p) => p.source !== "docker" && (p.process_name || p.pid)),
    [ports.data],
  );
  const projects = useMemo(() => {
    const byProject = new Map<string, number>();
    for (const p of ports.data ?? []) {
      if (!p.project_name) continue;
      byProject.set(p.project_name, (byProject.get(p.project_name) ?? 0) + 1);
    }
    return Array.from(byProject.entries()).sort((a, b) => b[1] - a[1]);
  }, [ports.data]);

  if (host.isPending) {
    return <LoadingState variant="block" />;
  }

  if (host.isError) {
    return <ErrorState error={host.error} onRetry={() => void host.refetch()} />;
  }

  const data = host.data!;

  return (
    <div>
      <FreshnessWarning host={data} />

      <PageHeader
        breadcrumbs={[{ label: "Hosts", href: "/hosts" }, { label: data.hostname }]}
        title={data.hostname}
        description={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mt-1 text-sm text-muted-foreground">
            <span className="flex items-center gap-1.5"><Server className="size-4" /> {data.operating_system}{data.os_version ? ` · ${data.os_version}` : ""}</span>
            {data.architecture && <span className="flex items-center gap-1.5"><Cpu className="size-4" /> {data.architecture}</span>}
            <span className="flex items-center gap-1.5 font-mono text-xs border border-border px-1.5 py-0.5 rounded-md">Agent: {data.agent_version ?? "—"}</span>
            <span className="flex items-center gap-1.5"><HostStatus host={data} /></span>
            <span className="flex items-center gap-1.5"><DockerStatus available={data.docker_available} /></span>
          </div>
        }
      />

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">
            <Server className="size-3.5" aria-hidden="true" /> Overview
          </TabsTrigger>
          <TabsTrigger value="ports">
            <HardDrive className="size-3.5" aria-hidden="true" /> Ports
            {ports.data && <span className="text-muted-foreground">({ports.data.length})</span>}
          </TabsTrigger>
          <TabsTrigger value="docker">
            <Cpu className="size-3.5" aria-hidden="true" /> Docker
            {dockerPorts.length > 0 && <span className="text-muted-foreground">({dockerPorts.length})</span>}
          </TabsTrigger>
          <TabsTrigger value="processes">
            <Terminal className="size-3.5" aria-hidden="true" /> Processes
            {processPorts.length > 0 && <span className="text-muted-foreground">({processPorts.length})</span>}
          </TabsTrigger>
          <TabsTrigger value="projects">
            <Folder className="size-3.5" aria-hidden="true" /> Projects
            {projects.length > 0 && <span className="text-muted-foreground">({projects.length})</span>}
          </TabsTrigger>
          <TabsTrigger value="activity">
            <Activity className="size-3.5" aria-hidden="true" /> Activity
          </TabsTrigger>
          <TabsTrigger value="diagnostics">
            <Server className="size-3.5" aria-hidden="true" /> Diagnostics
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="pt-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card className="border-border bg-card">
              <CardContent className="pt-6">
                <h3 className="text-sm font-medium mb-4 text-foreground">Infrastructure Summary</h3>
                <div className="grid grid-cols-2 gap-y-4 text-sm">
                  <div className="space-y-1">
                    <span className="text-muted-foreground">Total bindings</span>
                    <p className="text-xl font-semibold">{ports.data?.length ?? 0}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-muted-foreground">Docker sourced</span>
                    <p className="text-xl font-semibold">{dockerPorts.length}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-muted-foreground">Process sourced</span>
                    <p className="text-xl font-semibold">{processPorts.length}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-muted-foreground">System sourced</span>
                    <p className="text-xl font-semibold">{(ports.data ?? []).filter(p => p.source === "system").length}</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="border-border bg-card">
              <CardContent className="pt-6">
                <h3 className="text-sm font-medium mb-4 text-foreground">System Information</h3>
                <DetailRow label="UUID" value={data.id} />
                <DetailRow label="First seen" value={formatAbsoluteTime(data.first_seen)} />
                <DetailRow label="Last seen" value={formatAbsoluteTime(data.last_seen)} />
                <DetailRow label="Reported status" value={data.status} />
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="ports" className="pt-4">
          {ports.isPending ? (
            <LoadingState variant="table" />
          ) : ports.isError ? (
            <ErrorState error={ports.error} onRetry={() => void ports.refetch()} />
          ) : (
            <PortTable ports={ports.data ?? []} showHost={false} />
          )}
        </TabsContent>

        <TabsContent value="docker" className="pt-4">
          {!data.docker_available ? (
            <EmptyState
              icon={Cpu}
              title="Docker is not available on this host"
              description="The agent reported no usable Docker CLI/daemon at last heartbeat."
            />
          ) : dockerPorts.length === 0 ? (
            <EmptyState
              icon={Cpu}
              title="No Docker-published ports"
              description="Docker is available on this host, but no current bindings are Docker-sourced."
            />
          ) : (
            <PortTable ports={dockerPorts} showHost={false} />
          )}
        </TabsContent>

        <TabsContent value="processes" className="pt-4">
          {processPorts.length === 0 ? (
            <EmptyState
              icon={Terminal}
              title="No process-owned ports"
              description="No current bindings report a native process owner."
            />
          ) : (
            <PortTable ports={processPorts} showHost={false} />
          )}
        </TabsContent>

        <TabsContent value="projects" className="pt-4">
          {projects.length === 0 ? (
            <EmptyState
              icon={Folder}
              title="No project metadata"
              description="No current bindings on this host report a detected project."
            />
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {projects.map(([name, count]) => (
                <Link key={name} href={`/projects/${encodeURIComponent(name)}`} className="rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <Card className="border-border bg-card transition-colors hover:border-primary/40">
                    <CardContent className="flex items-center justify-between py-4">
                      <span className="truncate text-sm font-medium text-foreground">{name}</span>
                      <span className="text-xs text-muted-foreground">
                        {count} port{count === 1 ? "" : "s"}
                      </span>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="activity" className="pt-4">
          <ActivityFeed hostId={hostId} limit={100} />
        </TabsContent>

        <TabsContent value="diagnostics" className="pt-4">
          <HostDiagnosticsTab hostId={hostId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function HostDiagnosticsTab({ hostId }: { hostId: string }) {
  const diagnostics = useHostDiagnostics(hostId);

  if (diagnostics.isPending) {
    return <LoadingState variant="block" />;
  }

  if (diagnostics.isError) {
    return <ErrorState error={diagnostics.error} onRetry={() => void diagnostics.refetch()} />;
  }

  const data = diagnostics.data!;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <Card className="border-border bg-card">
        <CardContent className="pt-6 space-y-4">
          <h3 className="text-sm font-medium text-foreground">Health Status</h3>
          <DetailRow label="Health State" value={data.host.health_state ?? "—"} />
          <DetailRow label="Health Reason" value={data.host.health_reason ?? "—"} />
          <DetailRow label="Agent Version" value={data.host.agent_version ?? "—"} />
          <DetailRow label="Docker Discovery" value={data.host.docker_available ? "Available" : "Unavailable"} />
        </CardContent>
      </Card>

      <Card className="border-border bg-card">
        <CardContent className="pt-6 space-y-4">
          <h3 className="text-sm font-medium text-foreground">Telemetry Activity</h3>
          <DetailRow label="Last Ingestion" value={data.last_scan_observed_at ? formatAbsoluteTime(data.last_scan_observed_at) : "Never"} />
          <DetailRow label="Age Seconds" value={String(data.host.age_seconds)} />
          {data.host.snapshot_age_seconds != null && (
            <DetailRow label="Snapshot Age Seconds" value={String(data.host.snapshot_age_seconds)} />
          )}
          <DetailRow label="Stale Threshold" value={`${data.stale_threshold_seconds}s`} />
          <DetailRow label="Offline Threshold" value={`${data.offline_threshold_seconds}s`} />
        </CardContent>
      </Card>

      <CompatibilityCard host={data.host} />
      <ProbeCapabilityCard capability={data.probe_capability ?? "unknown"} />
    </div>
  );
}


