"use client";

import Link from "next/link";
import { AlertTriangle, CircleCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { useConflicts } from "@/hooks/use-conflicts";
import { HardDrive } from "lucide-react";

/**
 * Every entry here comes directly from GET /api/conflicts, which is
 * strictly a same-host reservation-vs-actual-owner mismatch (see
 * backend/app/services/conflict_service.py -- it always compares a
 * reservation's own host_id against that SAME host's current
 * observation, never across hosts). This page never synthesizes a
 * cross-host conflict; the same port reserved/active on two different
 * hosts is expected and never shown here.
 */
export default function ConflictsPage() {
  const conflicts = useConflicts();

  if (conflicts.isPending) {
    return (
      <div>
        <PageHeader title="Conflicts" description="Reservations whose port is currently owned by something else." />
        <LoadingState variant="cards" rows={4} />
      </div>
    );
  }

  if (conflicts.isError) {
    return (
      <div>
        <PageHeader title="Conflicts" description="Reservations whose port is currently owned by something else." />
        <ErrorState error={conflicts.error} onRetry={() => void conflicts.refetch()} />
      </div>
    );
  }

  const items = conflicts.data ?? [];
  
  const groupedConflicts = items.reduce((acc, conflict) => {
    const key = conflict.host_id;
    if (!acc[key]) {
      acc[key] = { hostname: conflict.hostname, conflicts: [] };
    }
    acc[key].conflicts.push(conflict);
    return acc;
  }, {} as Record<string, { hostname: string, conflicts: typeof items }>);

  return (
    <div>
      <PageHeader title="Conflicts" description="Reservations whose port is currently owned by something else." />

      {items.length === 0 ? (
        <EmptyState
          icon={CircleCheck}
          title="No conflicts"
          description="Every reservation matches what's currently active on its host."
        />
      ) : (
        <div className="space-y-6">
          {Object.entries(groupedConflicts).map(([hostId, group]) => (
            <Card key={hostId} className="border-red-500/20 bg-card overflow-hidden">
              <CardHeader className="bg-red-500/5 py-3 border-b border-red-500/20">
                <CardTitle className="text-sm font-semibold flex items-center gap-2 text-red-500/90">
                  <HardDrive className="size-4" />
                  <Link href={`/hosts/${hostId}`} className="hover:underline">
                    {group.hostname}
                  </Link>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="divide-y divide-red-500/10">
                  {group.conflicts.map((conflict, index) => (
                    <div
                      key={`${conflict.port}-${conflict.protocol}-${index}`}
                      className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between hover:bg-muted/30 transition-colors"
                    >
                      <div className="flex items-start gap-3">
                        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-red-500/10 text-red-400">
                          <AlertTriangle className="size-4" aria-hidden="true" />
                        </span>
                        <div>
                          <p className="text-sm font-medium text-foreground">
                            <span className="font-mono">
                              {conflict.port}/{conflict.protocol}
                            </span>
                          </p>
                          <p className="text-sm text-muted-foreground">{conflict.reason}</p>
                        </div>
                      </div>
                      <div className="flex flex-col gap-0.5 text-xs text-muted-foreground sm:text-right">
                        <span>
                          Reserved for <span className="text-foreground font-medium">{conflict.reserved_for_project}</span>
                          {conflict.reserved_for_service ? ` / ${conflict.reserved_for_service}` : ""}
                        </span>
                        <span>
                          Actually used by{" "}
                          <span className="text-foreground font-medium">
                            {conflict.actual_project ?? conflict.actual_container_name ?? conflict.actual_process_name ?? "unknown"}
                          </span>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

