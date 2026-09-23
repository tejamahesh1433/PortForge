"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { AlertTriangle, Info, Trash2 } from "lucide-react";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { ConfirmDialog } from "@/components/controls/confirm-dialog";
import { StatusBadge } from "@/components/status/status-badge";
import { BindProbeBadge, getBindProbeMeta } from "@/components/status/bind-probe-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useAllocation, useReleaseAllocation, useVerifyAllocation } from "@/hooks/use-allocations";
import { formatAbsoluteTime } from "@/lib/utils/format";
import { toast } from "@/components/ui/toast";

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}

export default function AllocationDetailPage() {
  const params = useParams<{ allocationId: string }>();
  const allocation = useAllocation(params.allocationId);
  const releaseMutation = useReleaseAllocation();
  const verifyMutation = useVerifyAllocation();
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (allocation.isPending) return <LoadingState variant="block" />;
  if (allocation.isError) return <ErrorState error={allocation.error} onRetry={() => void allocation.refetch()} />;

  const data = allocation.data!;

  const verify = () => {
    verifyMutation.mutate(data.allocation_id, {
      onSuccess: () => toast.add({ type: "success", title: "Allocation verified", description: "Verification refreshed from the target host." }),
      onError: (error) => toast.add({ type: "error", title: "Verification failed", description: error instanceof Error ? error.message : "Central could not verify this allocation." }),
    });
  };

  const confirmRelease = () => {
    releaseMutation.mutate(data.allocation_id, {
      onSuccess: () => {
        toast.add({
          type: "success",
          title: "Allocation released",
          description: `${data.allocations.length} binding${data.allocations.length === 1 ? "" : "s"} released.`,
        });
        setConfirmOpen(false);
      },
      onError: (error) => {
        toast.add({
          type: "error",
          title: "Failed to release allocation",
          description: error instanceof Error ? error.message : "Unknown error occurred.",
        });
      },
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumbs={[{ label: "Allocations", href: "/allocations" }, { label: data.project }]}
        title={data.project}
        description={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mt-1 text-sm text-muted-foreground">
            <span className="font-mono text-xs">{data.allocation_id}</span>
            <StatusBadge value={data.status} />
          </div>
        }
      />

      {data.status === "active" && (
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" size="sm" onClick={verify} disabled={verifyMutation.isPending}>
            {verifyMutation.isPending ? "Verifying…" : "Verify allocation"}
          </Button>
          <Button variant="destructive" size="sm" onClick={() => setConfirmOpen(true)} disabled={verifyMutation.isPending}>
            <Trash2 className="size-4" /> Release allocation
          </Button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <Card className="border-border bg-card">
          <CardContent className="pt-6 space-y-1">
            <h3 className="text-sm font-medium mb-3 text-foreground">Allocation identity</h3>
            <DetailRow label="Allocation ID" value={<span className="font-mono text-xs">{data.allocation_id}</span>} />
            <DetailRow
              label="Host"
              value={
                <Link href={`/hosts/${data.host.id}`} className="text-primary hover:underline">
                  {data.host.hostname}
                </Link>
              }
            />
            <DetailRow label="Project" value={data.project} />
            <DetailRow
              label="Request ID"
              value={data.request_id ? <span className="font-mono text-xs">{data.request_id}</span> : "—"}
            />
            <DetailRow label="Created" value={formatAbsoluteTime(data.created_at)} />
            {data.released_at && <DetailRow label="Released" value={formatAbsoluteTime(data.released_at)} />}
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardContent className="pt-6 space-y-1">
            <h3 className="text-sm font-medium mb-3 text-foreground">Validation at time of read</h3>
            <DetailRow label="Host health state" value={<StatusBadge value={data.validation.host_health_state} compact={false} />} />
            <DetailRow label="Snapshot age" value={`${data.validation.snapshot_age_seconds}s`} />
            <DetailRow label="Bundle probe evidence" value={<BindProbeBadge value={data.validation.bind_probe} />} />
            <p className="mt-3 text-xs text-muted-foreground">
              Verified free means the binding was free at the time of the last probe; it is not a future availability guarantee.
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              An allocation reserves this binding in PortForge&apos;s own state. It does not, by itself, prove any
              application is currently listening on it -- that is a separate, physically observed fact (see the
              host&apos;s Ports tab for what is actually bound right now).
            </p>
          </CardContent>
        </Card>
      </div>

      <div>
        <h3 className="text-sm font-medium mb-3 text-foreground">Bindings</h3>
        {data.allocations.length === 0 ? (
          <EmptyState
            icon={Info}
            title={data.status === "released" ? "No bindings to show" : "No bindings recorded"}
            description={
              data.status === "released"
                ? "This allocation has been released. Its individual bindings are resolved live against current reservations, which no longer exist for a released allocation -- this is not a historical snapshot, and it is expected to be empty."
                : "This allocation currently has no recorded bindings."
            }
          />
        ) : (
          <div className="space-y-2">
            {data.allocations.map((entry) => {
              const probeMeta = getBindProbeMeta(entry.bind_probe);
              return (
                <Card key={entry.reservation_id} className="border-border bg-card">
                  <CardContent className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <div>
                      <p className="font-mono text-sm font-medium">
                        {entry.port}/{entry.protocol}
                        {entry.bind_address ? ` · ${entry.bind_address}` : ""}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {entry.name} · purpose: {entry.purpose} · reservation {entry.reservation_id.slice(0, 8)}
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <BindProbeBadge value={entry.bind_probe} />
                      <p className="max-w-sm text-right text-[11px] text-muted-foreground">{probeMeta.description}</p>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      <Card className="border-border bg-card">
        <CardContent className="flex gap-3 py-4 text-sm text-muted-foreground">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium text-foreground">Workflow and config-mutation state is local to the project host</p>
            <p className="mt-1">
              If this allocation was created by <code className="text-xs">portforge workflow apply</code> or has an
              associated config mutation (dotenv/Compose/Kubernetes), that state lives only in the coding agent&apos;s
              own project directory (<code className="text-xs">.portforge/workflows/</code> and{" "}
              <code className="text-xs">.portforge/mutations/</code>) -- Central never receives it, so this dashboard
              cannot show it. Inspect it on the host itself with{" "}
              <code className="text-xs">portforge workflow status --request-id &lt;id&gt;</code> or{" "}
              <code className="text-xs">portforge config status &lt;mutation_id&gt;</code>.
            </p>
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Release allocation?"
        description={`This releases all ${data.allocations.length} binding${data.allocations.length === 1 ? "" : "s"} in this bundle for '${data.project}' on ${data.host.hostname}. This cannot be undone from here.`}
        confirmLabel="Release"
        destructive
        onConfirm={confirmRelease}
        isConfirming={releaseMutation.isPending}
      />
    </div>
  );
}
