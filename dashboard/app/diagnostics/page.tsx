"use client";

import { AlertTriangle, HardDrive, Network, Server, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { PageHeader } from "@/components/layout/page-header";
import { LoadingState } from "@/components/feedback/loading-state";
import { ErrorState } from "@/components/feedback/error-state";
import { useGlobalDiagnostics } from "@/hooks/use-health";
import { formatAbsoluteTime } from "@/lib/utils/format";

export default function DiagnosticsPage() {
  const diagnostics = useGlobalDiagnostics();

  if (diagnostics.isPending) {
    return (
      <div className="space-y-6">
        <PageHeader title="Global Diagnostics" description="System health and internal telemetry." />
        <LoadingState variant="cards" rows={2} />
      </div>
    );
  }

  if (diagnostics.isError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Global Diagnostics" description="System health and internal telemetry." />
        <ErrorState error={diagnostics.error} onRetry={() => void diagnostics.refetch()} />
      </div>
    );
  }

  const data = diagnostics.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Global Diagnostics"
        description="System health and internal telemetry."
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="border-border bg-card">
          <CardHeader className="pb-4">
            <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Server className="size-4" /> System Health
            </h3>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground">Central Service</span>
              <span className="font-medium text-foreground capitalize">{data.status}</span>
            </div>
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground">Database Connectivity</span>
              <span className="font-medium text-foreground capitalize">{data.database}</span>
            </div>
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground">Version</span>
              <span className="font-medium text-foreground">{data.version}</span>
            </div>
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground">Latest Ingestion Time</span>
              <span className="font-medium text-foreground">
                {data.latest_ingestion_time ? formatAbsoluteTime(data.latest_ingestion_time) : "Never"}
              </span>
            </div>
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader className="pb-4">
            <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Network className="size-4" /> Fleet Telemetry
            </h3>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground">Total Hosts</span>
              <span className="font-medium text-foreground">{data.host_count_total}</span>
            </div>
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground flex items-center gap-2">
                <ShieldCheck className="size-3.5 text-emerald-500" /> Healthy Hosts
              </span>
              <span className="font-medium text-foreground">{data.host_count_healthy}</span>
            </div>
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground flex items-center gap-2">
                <AlertTriangle className="size-3.5 text-amber-500" /> Stale Hosts
              </span>
              <span className="font-medium text-foreground">{data.host_count_stale}</span>
            </div>
            <div className="flex justify-between border-b border-border py-2 text-sm">
              <span className="text-muted-foreground flex items-center gap-2">
                <HardDrive className="size-3.5 text-red-500" /> Offline Hosts
              </span>
              <span className="font-medium text-foreground">{data.host_count_offline}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
