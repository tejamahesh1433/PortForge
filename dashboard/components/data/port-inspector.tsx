"use client";

import { useState } from "react";
import { Activity, Box, Check, Clock, Container, Copy, HardDrive, Hash, Network, Terminal } from "lucide-react";
import type { PortObservationOut } from "@/lib/types/api";
import { useHost } from "@/hooks/use-hosts";
import { ActivityFeed } from "@/components/activity/activity-feed";
import { FreshnessWarning } from "@/components/status/freshness-warning";
import { PortSourceBadge } from "@/components/status/port-source-badge";
import { PortStateBadge } from "@/components/status/port-state-badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";

interface PortInspectorProps { port: PortObservationOut | null; open: boolean; onOpenChange: (open: boolean) => void }

function CopyValue({ label, value }: { label: string; value: string | number }) {
  const [copied, setCopied] = useState(false);
  const text = String(value);
  return <div className="grid grid-cols-[7rem_minmax(0,1fr)_auto] items-center gap-2 text-sm"><span className="text-muted-foreground">{label}</span><span className="truncate font-mono text-xs" title={text}>{text}</span><Button variant="ghost" size="icon-sm" aria-label={`Copy ${label}`} onClick={() => { void navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1200); }}>{copied ? <Check className="size-3.5 text-emerald-400" /> : <Copy className="size-3.5" />}</Button></div>;
}

function Section({ icon: Icon, title, children }: { icon: typeof Hash; title: string; children: React.ReactNode }) {
  return <section className="space-y-2"><h3 className="flex items-center gap-2 border-b border-border pb-1 text-sm font-semibold"><Icon className="size-4 text-muted-foreground" aria-hidden="true" />{title}</h3>{children}</section>;
 }

export function PortInspector({ port, open, onOpenChange }: PortInspectorProps) {
  const host = useHost(port?.host_id);
  if (!port) return null;
  const ownerValues = port.source === "docker" ? [port.container_name, port.container_id, port.container_image] : [port.process_name, port.pid, port.process_path];
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="w-[440px] overflow-y-auto sm:max-w-md"><SheetHeader className="mb-5"><SheetTitle className="flex items-center gap-2 font-mono text-2xl"><Hash className="size-5" />{port.port}/{port.protocol}</SheetTitle><SheetDescription className="flex items-center gap-2"><PortStateBadge state={port.state} /><PortSourceBadge source={port.source} /></SheetDescription></SheetHeader><div className="space-y-6">{host.data ? <FreshnessWarning host={host.data} /> : null}<Section icon={Hash} title="Identity"><CopyValue label="Port" value={`${port.port}/${port.protocol}`} /><CopyValue label="Binding ID" value={port.id} /></Section><Section icon={HardDrive} title="Host"><CopyValue label="Hostname" value={port.host_hostname ?? "Unknown host"} /><CopyValue label="Host ID" value={port.host_id} /><CopyValue label="Host and port" value={`${port.host_hostname ?? port.host_id}:${port.port}`} /></Section><Section icon={Network} title="Binding"><CopyValue label="Bind address" value={port.bind_address} /><div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">State</span><PortStateBadge state={port.state} /></div><div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">Source</span><PortSourceBadge source={port.source} /></div></Section>{ownerValues.some(Boolean) ? <Section icon={port.source === "docker" ? Container : Terminal} title={port.source === "docker" ? "Docker mapping" : "Owner"}>{port.source === "docker" ? <>{port.container_name ? <CopyValue label="Container" value={port.container_name} /> : null}{port.container_id ? <CopyValue label="Container ID" value={port.container_id} /> : null}{port.container_image ? <CopyValue label="Image" value={port.container_image} /> : null}{port.container_port ? <CopyValue label="Container port" value={port.container_port} /> : null}</> : <>{port.process_name ? <CopyValue label="Process" value={port.process_name} /> : null}{port.pid ? <CopyValue label="PID" value={port.pid} /> : null}{port.process_path ? <CopyValue label="Path" value={port.process_path} /> : null}</>}</Section> : null}{port.project_name || port.service_name || port.purpose ? <Section icon={Box} title="Project context">{port.project_name ? <CopyValue label="Project" value={port.project_name} /> : null}{port.service_name ? <CopyValue label="Service" value={port.service_name} /> : null}{port.purpose ? <CopyValue label="Purpose" value={port.purpose} /> : null}</Section> : null}<Section icon={Clock} title="State and freshness"><CopyValue label="First seen" value={new Date(port.first_seen).toLocaleString()} /><CopyValue label="Last seen" value={new Date(port.last_seen).toLocaleString()} /><CopyValue label="Observed" value={new Date(port.observed_at).toLocaleString()} /></Section><Section icon={Activity} title="History"><ActivityFeed hostId={port.host_id} port={port.port} limit={10} compact /></Section></div></SheetContent></Sheet>;
}

