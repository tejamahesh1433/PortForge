"use client";

import { useState } from "react";
import { CircleAlert, Lightbulb } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { useHosts } from "@/hooks/use-hosts";
import { useRecommendation } from "@/hooks/use-recommendation";
import { ReservationModal } from "@/components/forms/reservation-modal";
import { Button } from "@/components/ui/button";
import { Lock } from "lucide-react";

// Mirrors backend/app/services/recommendation_service.py's _DEFAULT_RANGES
// exactly -- Central only recognizes these six service types; anything
// else deterministically comes back with recommended_port: null.
const SERVICE_TYPES = ["frontend", "api", "postgres", "mysql", "redis", "generic"] as const;

export default function RecommendationsPage() {
  const hosts = useHosts({ limit: 500 });
  const [hostId, setHostId] = useState<string>("");
  const [serviceType, setServiceType] = useState<string>("");
  const [protocol, setProtocol] = useState<"tcp" | "udp">("tcp");

  const recommendation = useRecommendation(
    hostId && serviceType ? { host_id: hostId, service_type: serviceType, protocol } : undefined,
  );

  return (
    <div>
      <PageHeader
        title="Recommendations"
        description="A central suggestion based on cached data -- never a verified-available answer."
      />

      <Card className="mb-6 max-w-2xl border-border bg-card">
        <CardContent className="grid grid-cols-1 gap-3 pt-4 sm:grid-cols-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Host</label>
            <Select value={hostId} onValueChange={(value) => setHostId(value ?? "")}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select a host">
                  {(value: string | null) =>
                    value ? (hosts.data?.items?.find((h) => h.id === value)?.hostname ?? value) : "Select a host"
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(hosts.data?.items ?? []).map((host) => (
                  <SelectItem key={host.id} value={host.id}>
                    {host.hostname}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Service type</label>
            <Select value={serviceType} onValueChange={(value) => setServiceType(value ?? "")}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select a type" />
              </SelectTrigger>
              <SelectContent>
                {SERVICE_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {type}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Protocol</label>
            <Select value={protocol} onValueChange={(value) => setProtocol(value as "tcp" | "udp")}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tcp">TCP</SelectItem>
                <SelectItem value="udp">UDP</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {!hostId || !serviceType ? (
        <EmptyState
          icon={Lightbulb}
          title="Choose a host and service type"
          description="Central will suggest a port based on what it currently has on file for that host."
        />
      ) : recommendation.isPending ? (
        <LoadingState />
      ) : recommendation.isError ? (
        <ErrorState error={recommendation.error} onRetry={() => void recommendation.refetch()} />
      ) : (
        recommendation.data && (
          <div className="max-w-2xl space-y-4">
            <Card className="border-border bg-card">
              <CardContent className="flex items-center justify-between pt-4">
                <div>
                  <p className="text-xs text-muted-foreground">Recommended port</p>
                  <p className="font-mono text-3xl font-semibold text-foreground">
                    {recommendation.data.recommended_port ?? "—"}
                  </p>
                </div>
                <div className="text-right text-xs text-muted-foreground">
                  <p>{recommendation.data.candidates_considered} candidates considered</p>
                  <p>{recommendation.data.known_conflicts_excluded.length} excluded as occupied/reserved</p>
                </div>
              </CardContent>
              <div className="border-t border-border bg-muted/30 px-6 py-3 flex justify-end">
                <ReservationModal 
                  defaultHostId={hostId}
                  defaultPort={recommendation.data.recommended_port?.toString()}
                  defaultProtocol={protocol}
                  defaultService={serviceType}
                  trigger={
                    <Button variant="default" size="sm" className="gap-2" disabled={!recommendation.data.recommended_port}>
                      <Lock className="size-3.5" />
                      Reserve this port
                    </Button>
                  }
                />
              </div>
            </Card>

            <Alert>
              <CircleAlert className="size-4" aria-hidden="true" />
              <AlertTitle>Central suggestion, not a verified answer</AlertTitle>
              <AlertDescription>{recommendation.data.basis}</AlertDescription>
            </Alert>
          </div>
        )
      )}
    </div>
  );
}
