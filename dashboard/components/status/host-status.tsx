import { cn } from "cn";
import { formatRelativeTime } from "@/lib/utils/format";
import type { HostOut } from "@/lib/types/api";
import { getHostHealthState } from "@/lib/utils/host-health";
import { StatusBadge } from "./status-badge";

/** Host health indicator with a pulsing dot for "HEALTHY" (Phase 7C.2). */
export function HostStatus({ host, className }: { host: Pick<HostOut, "last_seen"> & { health_state?: string }; className?: string }) {
  const healthState = getHostHealthState(host);
  const isHealthy = healthState === "HEALTHY";
  const isStale = healthState === "STALE";

  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className="relative flex size-2">
        {isHealthy && (
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-75" />
        )}
        <span
          className={cn(
            "relative inline-flex size-2 rounded-full",
            isHealthy ? "bg-emerald-400" : isStale ? "bg-amber-400" : "bg-zinc-500"
          )}
        />
      </span>
      <StatusBadge value={healthState.toLowerCase()} />
      <span className="text-xs text-muted-foreground">{formatRelativeTime(host.last_seen)}</span>
    </span>
  );
}
