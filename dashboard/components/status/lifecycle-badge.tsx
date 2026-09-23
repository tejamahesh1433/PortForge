import { cn } from "cn";
import { CircleCheck, CircleOff } from "lucide-react";

export type LifecycleState = "ACTIVE" | "DECOMMISSIONED";

interface LifecycleBadgeProps {
  /** lifecycle_state from HostOut; undefined/null treated as ACTIVE for back-compat. */
  state?: LifecycleState | null;
  className?: string;
}

/**
 * Distinct from health badges -- shows the administrative lifecycle state
 * of a host record (ACTIVE vs DECOMMISSIONED), not its liveness signal.
 * Never overloaded with health colors so the two concepts stay visually
 * separate at a glance.
 */
export function LifecycleBadge({ state, className }: LifecycleBadgeProps) {
  const resolved: LifecycleState = state === "DECOMMISSIONED" ? "DECOMMISSIONED" : "ACTIVE";

  if (resolved === "DECOMMISSIONED") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
          "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
          className,
        )}
      >
        <CircleOff className="size-3.5" aria-hidden="true" />
        Decommissioned
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
        "border-emerald-500/20 bg-emerald-500/10 text-emerald-400",
        className,
      )}
    >
      <CircleCheck className="size-3.5" aria-hidden="true" />
      Active
    </span>
  );
}
