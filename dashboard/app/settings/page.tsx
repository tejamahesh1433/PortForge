"use client";

import { CircleCheck, ExternalLink, WifiOff } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { useHealth } from "@/hooks/use-health";
import { PORTFORGE_API_URL } from "@/lib/api/config";

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-xs text-foreground">{value}</span>
    </div>
  );
}

export default function SettingsPage() {
  const health = useHealth();

  return (
    <div>
      <PageHeader title="Settings" description="PortForge Central connection and dashboard configuration." />

      <div className="max-w-xl space-y-6">
        <Card className="border-border bg-card">
          <CardHeader>
            <CardTitle className="text-sm">Central connection</CardTitle>
          </CardHeader>
          <CardContent>
            <InfoRow label="API base URL" value={PORTFORGE_API_URL} />
            {health.isPending ? (
              <div className="py-2">
                <LoadingState variant="block" />
              </div>
            ) : health.isError ? (
              <div className="py-2">
                <ErrorState error={health.error} onRetry={() => void health.refetch()} />
              </div>
            ) : (
              health.data && (
                <>
                  <InfoRow
                    label="Status"
                    value={health.data.status === "ok" ? "Connected" : health.data.status}
                  />
                  <InfoRow label="Database" value={health.data.database} />
                  <InfoRow label="Central version" value={health.data.version} />
                </>
              )
            )}
            <div className="pt-3">
              <Button
                variant="outline"
                size="sm"
                nativeButton={false}
                render={<a href={`${PORTFORGE_API_URL}/docs`} target="_blank" rel="noreferrer" />}
              >
                Open Central API docs
                <ExternalLink className="size-3.5" aria-hidden="true" />
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader>
            <CardTitle className="text-sm">Configuration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              The Central API URL is read from the{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">
                NEXT_PUBLIC_PORTFORGE_API_URL
              </code>{" "}
              environment variable at build/dev time. See the dashboard{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">.env.example</code>{" "}
              for local development setup.
            </p>
            <div className="flex items-center gap-1.5 pt-1">
              {health.data?.status === "ok" ? (
                <CircleCheck className="size-3.5 text-emerald-400" aria-hidden="true" />
              ) : (
                <WifiOff className="size-3.5 text-red-400" aria-hidden="true" />
              )}
              <span>
                {health.data?.status === "ok"
                  ? "This URL is currently reachable."
                  : "This URL is not currently reachable."}
              </span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}



