"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow, format } from "date-fns";
import { Server, Network, Lock, Zap, Eye, EyeOff } from "lucide-react";
import { listActivity } from "@/lib/api/activity";
import type { ActivityEventOut } from "@/lib/types/api";
import { useHosts } from "@/hooks/use-hosts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { FilterSelect } from "@/components/controls/filter-select";
import { ActiveFilters } from "@/components/controls/active-filters";

interface ActivityFeedProps {
  hostId?: string;
  port?: number;
  limit?: number;
  compact?: boolean;
  filterable?: boolean;
  // Controlled filter state, for callers (e.g. the top-level /activity page)
  // that need these persisted in the URL. Uncontrolled (internal useState)
  // when omitted -- e.g. the host-detail Activity tab, which has no need
  // to fight the page's own URL params over the same query keys.
  eventType?: string;
  onEventTypeChange?: (value: string) => void;
  protocol?: string;
  onProtocolChange?: (value: string) => void;
  showEphemeral?: boolean;
  onShowEphemeralChange?: (value: boolean) => void;
}

export function isEphemeralActivity(event: ActivityEventOut): boolean {
  const project = typeof event.metadata_json?.project_name === "string" ? event.metadata_json.project_name : null;
  return event.event_type.startsWith("PORT_") && event.protocol === "udp" && event.source === "process" && !project && (event.port ?? 0) >= 49152;
}

export function ActivityFeed({
  hostId,
  port,
  limit = 50,
  compact = false,
  filterable = false,
  eventType: controlledEventType,
  onEventTypeChange,
  protocol: controlledProtocol,
  onProtocolChange,
  showEphemeral: controlledShowEphemeral,
  onShowEphemeralChange,
}: ActivityFeedProps) {
  const [internalEventType, setInternalEventType] = useState("all");
  const [internalProtocol, setInternalProtocol] = useState("all");
  const [internalShowEphemeral, setInternalShowEphemeral] = useState(false);

  const eventType = controlledEventType ?? internalEventType;
  const setEventType = onEventTypeChange ?? setInternalEventType;
  const protocol = controlledProtocol ?? internalProtocol;
  const setProtocol = onProtocolChange ?? setInternalProtocol;
  const showEphemeral = controlledShowEphemeral ?? internalShowEphemeral;
  const setShowEphemeral = onShowEphemeralChange ?? setInternalShowEphemeral;
  const activity = useQuery({ queryKey: ["activity", hostId, port, limit], queryFn: ({ signal }) => listActivity({ host_id: hostId, port, limit }, signal) });
  const hosts = useHosts({ limit: 500 });
  const hostnameById = useMemo(() => new Map((hosts.data?.items ?? []).map((host) => [host.id, host.hostname])), [hosts.data]);
  const events = useMemo(() => (activity.data?.events ?? []).filter((event) => {
    if (!showEphemeral && isEphemeralActivity(event)) return false;
    if (eventType !== "all" && event.event_type !== eventType) return false;
    if (protocol !== "all" && event.protocol !== protocol) return false;
    return true;
  }), [activity.data, eventType, protocol, showEphemeral]);

  if (activity.isPending) return <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}</div>;
  if (activity.isError) return <ErrorState error={activity.error} onRetry={() => void activity.refetch()} />;

  return <div>
    {filterable ? <><div className="mb-3 flex flex-wrap items-center gap-2"><FilterSelect label="Event" value={eventType} onChange={setEventType} options={["PORT_APPEARED","PORT_DISAPPEARED","RESERVATION_CREATED","RESERVATION_RELEASED","HOST_ONLINE"].map((value) => ({ value, label: value.replaceAll("_", " ") }))} /><FilterSelect label="Protocol" value={protocol} onChange={setProtocol} options={[{ value: "tcp", label: "TCP" }, { value: "udp", label: "UDP" }]} /><Button variant="outline" size="sm" aria-pressed={showEphemeral} onClick={() => setShowEphemeral(!showEphemeral)}>{showEphemeral ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />} {showEphemeral ? "Hide ephemeral activity" : "Show ephemeral activity"}</Button></div><ActiveFilters filters={[...(eventType !== "all" ? [{ label: "Event", value: eventType, onRemove: () => setEventType("all") }] : []), ...(protocol !== "all" ? [{ label: "Protocol", value: protocol.toUpperCase(), onRemove: () => setProtocol("all") }] : [])]} onReset={() => { setEventType("all"); setProtocol("all"); setShowEphemeral(false); }} /></> : null}
    {!events.length ? <EmptyState icon={Zap} title="No activity matching filters" description={activity.data?.events.length ? "Raw events remain stored. Adjust filters or show ephemeral activity to broaden this view." : "Operational changes will appear here after Central records them."} /> : <div className="divide-y divide-border rounded-lg border border-border bg-card">{events.map((event) => <ActivityItem key={event.id} event={event} compact={compact} hostname={hostnameById.get(event.host_id)} />)}</div>}
  </div>;
}

function ActivityItem({ event, compact, hostname }: { event: ActivityEventOut; compact: boolean; hostname?: string }) {
  const isPort = event.event_type.startsWith("PORT_"); const isReservation = event.event_type.startsWith("RESERVATION_"); const Icon = isPort ? Network : isReservation ? Lock : event.event_type.startsWith("HOST_") ? Server : Zap;
  const project = typeof event.metadata_json?.project_name === "string" ? event.metadata_json.project_name : null;
  return <div className={`grid gap-3 px-3 ${compact ? "py-2" : "py-3"} sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center`}><span className="flex size-8 items-center justify-center rounded-md bg-muted text-muted-foreground"><Icon className="size-4" aria-hidden="true" /></span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline" className="font-mono text-[10px]">{event.event_type}</Badge>{event.port ? <span className="font-mono text-xs">{event.port}/{event.protocol}</span> : null}<span className="truncate text-sm font-medium">{event.summary}</span></div><p className="mt-1 truncate text-xs text-muted-foreground">{hostname ?? event.host_id.slice(0, 8)}{project ? ` · ${project}` : ""}{event.identity_context ? ` · ${event.identity_context}` : ""}</p></div><time className="text-xs text-muted-foreground sm:text-right" dateTime={event.timestamp} title={format(new Date(event.timestamp), "PPpp")}>{formatDistanceToNow(new Date(event.timestamp), { addSuffix: true })}</time></div>;
}

