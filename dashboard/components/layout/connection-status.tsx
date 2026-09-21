"use client";

import { Loader2, WifiOff } from "lucide-react";
import { cn } from "cn";
import { useHealth } from "@/hooks/use-health";
import { getStatusMeta } from "@/lib/utils/status-meta";

/** Central connection indicator, backed by GET /api/health -- shows a
 * real connected/degraded/unreachable state rather than assuming Central
 * is always up. The pulse animation communicates "live monitoring", not
 * decoration -- it stops once a definite state is known.
 */
export function ConnectionStatus() {
  const { data, isPending, isError } = useHealth();

  if (isPending) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        Connecting…
      </span>
    );
  }

  if (isError || !data) {
    return (
      <span className="flex items-center gap-1.5 rounded-full border border-red-500/20 bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-400">
        <WifiOff className="size-3.5" aria-hidden="true" />
        Central unreachable
      </span>
    );
  }

  const connected = data.status === "ok" && data.database === "connected";
  const meta = getStatusMeta(connected ? "online" : "offline");

  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
        meta.className,
      )}
      title={`Central v${data.version} — database ${data.database}`}
    >
      <span className="relative flex size-2">
        {connected && (
          <span
            className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-75", meta.dotClassName)}
          />
        )}
        <span className={cn("relative inline-flex size-2 rounded-full", meta.dotClassName)} />
      </span>
      {connected ? "Central connected" : `Central ${data.status}`}
    </span>
  );
}
