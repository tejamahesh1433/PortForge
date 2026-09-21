"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { AlertTriangle, Box, Container, Lightbulb, Lock, Network, Server, Trash2 } from "lucide-react";
import { MetricCard } from "@/components/data/metric-card";
import { PortTable } from "@/components/data/port-table";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { ReservationModal } from "@/components/forms/reservation-modal";
import { StatusBadge } from "@/components/status/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useProject } from "@/hooks/use-projects";
import { useDeleteDashboardReservation } from "@/hooks/use-reservations";
import { useRecommendation } from "@/hooks/use-recommendation";
import { formatRelativeTime } from "@/lib/utils/format";
import type { ActivityEventOut, PortObservationOut } from "@/lib/types/api";

const TABS = ["overview", "ports", "processes", "docker", "reservations", "conflicts", "activity", "recommend"] as const;

function ActivityRows({ events }: { events: ActivityEventOut[] }) {
  if (!events.length) return <EmptyState title="No project activity" description="No reliably project-linked events have been recorded yet." />;
  return <div className="space-y-2">{events.map((event) => (
    <Card key={event.id} className="border-border bg-card">
      <CardContent className="flex items-start justify-between gap-4 py-3">
        <div><p className="text-sm font-medium">{event.summary}</p><p className="mt-1 text-xs text-muted-foreground">{event.event_type}{event.port ? ` · ${event.port}/${event.protocol}` : ""}</p></div>
        <time className="shrink-0 text-xs text-muted-foreground">{formatRelativeTime(event.timestamp)}</time>
      </CardContent>
    </Card>
  ))}</div>;
}

function BindingCards({ ports, mode }: { ports: PortObservationOut[]; mode: "process" | "docker" }) {
  const filtered = ports.filter((port) => mode === "docker" ? port.source === "docker" : port.source === "process");
  if (!filtered.length) return <EmptyState icon={mode === "docker" ? Container : Box} title={`No ${mode} bindings`} description={`This project has no current ${mode}-backed bindings.`} />;
  return <div className="grid gap-3 md:grid-cols-2">{filtered.map((port) => (
    <Card key={port.id} className="border-border bg-card">
      <CardContent className="space-y-2 py-3">
        <div className="flex items-center justify-between"><Link href={`/hosts/${port.host_id}`} className="text-sm font-medium hover:text-primary">{port.host_hostname}</Link><span className="font-mono text-sm">{port.port}/{port.protocol}</span></div>
        <p className="text-sm text-foreground">{mode === "docker" ? (port.service_name || port.container_name || "Unknown container") : (port.process_name || "Unknown process")}</p>
        <p className="text-xs text-muted-foreground">{mode === "docker" ? `${port.bind_address} → ${port.container_port ?? "—"} · ${port.container_image ?? "—"}` : `PID ${port.pid ?? "—"} · ${port.purpose ?? "Unknown purpose"} · confidence ${port.detection_confidence ?? "—"}`}</p>
      </CardContent>
    </Card>
  ))}</div>;
}

export default function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const projectName = decodeURIComponent(params.projectId);
  const searchParams = useSearchParams();
  const router = useRouter();
  const requestedTab = searchParams.get("tab") ?? "overview";
  const tab = TABS.includes(requestedTab as typeof TABS[number]) ? requestedTab : "overview";
  const project = useProject(projectName);
  const deleteReservation = useDeleteDashboardReservation();
  const [targetHost, setTargetHost] = useState("");
  const [serviceType, setServiceType] = useState("");
  const [protocol, setProtocol] = useState<"tcp" | "udp">("tcp");
  const recommendation = useRecommendation(targetHost && serviceType ? { host_id: targetHost, service_type: serviceType, protocol } : undefined);

  const setTab = (value: string) => {
    const next = new URLSearchParams(searchParams.toString());
    next.set("tab", value);
    router.replace(`/projects/${encodeURIComponent(projectName)}?${next.toString()}`, { scroll: false });
  };

  const data = project.data;
  const hasStaleTopology = Boolean(data && (data.stale_host_count || data.offline_host_count));
  const processPorts = useMemo(() => data?.ports.items.filter((port) => port.source === "process") ?? [], [data]);
  const dockerPorts = useMemo(() => data?.ports.items.filter((port) => port.source === "docker") ?? [], [data]);

  if (project.isPending) return <LoadingState variant="block" />;
  if (project.isError) return <ErrorState error={project.error} onRetry={() => void project.refetch()} />;
  if (!data) return null;

  return (
    <div className="space-y-6">
      <PageHeader breadcrumbs={[{ label: "Projects", href: "/projects" }, { label: data.project_name }]} title={data.project_name} description={data.host_count > 1 ? `Multi-host project context across ${data.host_count} physical hosts.` : "Operational project context from Central."} />
      {hasStaleTopology && <div className="flex gap-3 rounded-lg border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-500"><AlertTriangle className="size-5 shrink-0" /><span>Part of this project&apos;s topology is based on last-known host state.</span></div>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <MetricCard label="Hosts" value={data.host_count} icon={Server} accent="blue" />
        <MetricCard label="Bindings" value={data.port_count} icon={Network} />
        <MetricCard label="Processes" value={data.process_count} icon={Box} />
        <MetricCard label="Docker" value={data.docker_binding_count} icon={Container} accent="blue" />
        <MetricCard label="Reservations" value={data.reservation_count} icon={Lock} accent="violet" />
        <MetricCard label="Conflicts" value={data.conflict_count} icon={AlertTriangle} accent={data.conflict_count ? "red" : "emerald"} />
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="flex h-auto flex-wrap">
          {TABS.map((item) => <TabsTrigger key={item} value={item} className="capitalize">{item}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="overview" className="space-y-4 pt-4">
          <h2 className="text-sm font-semibold">Host topology</h2>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {data.host_details.map((host) => <Card key={host.host_id} className="border-border bg-card"><CardHeader className="pb-2"><div className="flex items-center justify-between gap-2"><Link href={`/hosts/${host.host_id}`} className="truncate text-sm font-semibold hover:text-primary">{host.hostname}</Link><StatusBadge value={host.health_state} /></div></CardHeader><CardContent className="space-y-1 text-xs text-muted-foreground"><p>{host.operating_system} · {host.binding_count} project bindings</p><p>Snapshot {host.snapshot_age_seconds == null ? "not reported" : `${host.snapshot_age_seconds}s old`} · Docker {host.docker_available ? "available" : "unavailable"}</p></CardContent></Card>)}
          </div>
          <div className="grid gap-4 lg:grid-cols-2"><div><h2 className="mb-3 text-sm font-semibold">Recent activity</h2><ActivityRows events={data.activity.slice(0, 5)} /></div><div><h2 className="mb-3 text-sm font-semibold">Current conflicts</h2>{data.conflicts.length ? <div className="space-y-2">{data.conflicts.map((conflict) => <Card key={`${conflict.host_id}-${conflict.port}-${conflict.protocol}`} className="border-red-500/20 bg-red-500/5"><CardContent className="py-3 text-sm"><p className="font-medium">{conflict.hostname} · {conflict.port}/{conflict.protocol}</p><p className="mt-1 text-xs text-muted-foreground">{conflict.reason}</p></CardContent></Card>)}</div> : <EmptyState title="No project conflicts" description="No host-scoped reservation conflicts are active." />}</div></div>
        </TabsContent>
        <TabsContent value="ports" className="pt-4"><PortTable ports={data.ports.items} showHost /></TabsContent>
        <TabsContent value="processes" className="pt-4"><BindingCards ports={processPorts} mode="process" /></TabsContent>
        <TabsContent value="docker" className="pt-4"><BindingCards ports={dockerPorts} mode="docker" /></TabsContent>
        <TabsContent value="reservations" className="space-y-4 pt-4">
          <div className="flex justify-end"><ReservationModal defaultProject={data.project_name} trigger={<Button size="sm"><Lock className="size-4" /> Reserve for project</Button>} /></div>
          {data.reservations.items.length ? <div className="space-y-2">{data.reservations.items.map((reservation) => <Card key={reservation.id} className="border-border bg-card"><CardContent className="flex items-center justify-between gap-4 py-3"><div><p className="font-mono text-sm">{reservation.port}/{reservation.protocol}</p><p className="text-xs text-muted-foreground">{data.host_details.find((host) => host.host_id === reservation.host_id)?.hostname ?? reservation.host_id} · {reservation.service ?? reservation.purpose ?? "Project reservation"}</p></div><Button variant="ghost" size="icon-sm" aria-label="Release reservation" onClick={() => deleteReservation.mutate({ hostId: reservation.host_id, reservationId: reservation.id })}><Trash2 className="size-4" /></Button></CardContent></Card>)}</div> : <EmptyState title="No reservations" description="This project has no current Central reservations." />}
        </TabsContent>
        <TabsContent value="conflicts" className="pt-4">{data.conflicts.length ? <div className="space-y-2">{data.conflicts.map((conflict) => <Card key={`${conflict.host_id}-${conflict.port}`}><CardContent className="py-3"><p className="text-sm font-medium">{conflict.hostname} · {conflict.port}/{conflict.protocol}</p><p className="text-xs text-muted-foreground">{conflict.reason}</p></CardContent></Card>)}</div> : <EmptyState title="No project conflicts" description="Same numeric ports on different hosts remain independent and valid." />}</TabsContent>
        <TabsContent value="activity" className="pt-4"><ActivityRows events={data.activity} /></TabsContent>
        <TabsContent value="recommend" className="space-y-4 pt-4">
          <Card className="max-w-3xl border-border bg-card"><CardContent className="grid gap-3 py-4 sm:grid-cols-3">
            <Select value={targetHost} onValueChange={(value) => setTargetHost(value ?? "")}><SelectTrigger><SelectValue placeholder="Target host" /></SelectTrigger><SelectContent>{data.host_details.map((host) => <SelectItem key={host.host_id} value={host.host_id}>{host.hostname}</SelectItem>)}</SelectContent></Select>
            <Select value={serviceType} onValueChange={(value) => setServiceType(value ?? "")}><SelectTrigger><SelectValue placeholder="Purpose" /></SelectTrigger><SelectContent>{["frontend","api","postgres","mysql","redis","generic"].map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select>
            <Select value={protocol} onValueChange={(value) => setProtocol(value as "tcp" | "udp")}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="tcp">TCP</SelectItem><SelectItem value="udp">UDP</SelectItem></SelectContent></Select>
          </CardContent></Card>
          {!targetHost || !serviceType ? <EmptyState icon={Lightbulb} title="Choose a target host and purpose" description="Multi-host projects require an explicit target before Central can recommend a port." /> : recommendation.isPending ? <LoadingState /> : recommendation.isError ? <ErrorState error={recommendation.error} onRetry={() => void recommendation.refetch()} /> : recommendation.data ? <Card className="max-w-3xl border-border bg-card"><CardContent className="flex items-center justify-between gap-4 py-4"><div><p className="text-xs text-muted-foreground">Central suggestion</p><p className="font-mono text-3xl font-semibold">{recommendation.data.recommended_port ?? "—"}</p><p className="mt-1 text-xs text-muted-foreground">{recommendation.data.basis}</p></div><ReservationModal defaultHostId={targetHost} defaultPort={recommendation.data.recommended_port?.toString()} defaultProtocol={protocol} defaultService={serviceType} defaultProject={data.project_name} trigger={<Button disabled={!recommendation.data.recommended_port}><Lock className="size-4" /> Reserve</Button>} /></CardContent></Card> : null}
        </TabsContent>
      </Tabs>
    </div>
  );
}

