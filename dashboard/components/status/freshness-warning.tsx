"use client";

import { AlertTriangle } from "lucide-react";
import type { HostOut } from "@/lib/types/api";
import { formatAbsoluteTime } from "@/lib/utils/format";
import { getHostHealthState } from "@/lib/utils/host-health";
import { CheckAgainButton } from "@/components/hosts/check-again-button";

export function FreshnessWarning({
  host,
}: {
  host: Pick<HostOut, "id" | "hostname" | "last_seen" | "health_state">;
}) {
  const state = getHostHealthState(host);
  if (state === "HEALTHY") return null;
  return (
    <div
      role="status"
      className="mb-4 flex flex-col gap-3 rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-200 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <div>
          <p className="font-medium">Last-known data from {host.hostname}</p>
          <p className="mt-1 text-amber-200/75">
            Host is {state.toLowerCase()}. Last successful snapshot: {formatAbsoluteTime(host.last_seen)}.
            Bindings shown for this host are retained history and may no longer be current.
          </p>
        </div>
      </div>
      <CheckAgainButton
        hostId={host.id}
        hostname={host.hostname}
        className="shrink-0 border-amber-500/30 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20"
      />
    </div>
  );
}
