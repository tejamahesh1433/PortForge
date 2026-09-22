import { AlertTriangle, CircleCheck, CircleOff, Clock, HelpCircle, type LucideIcon } from "lucide-react";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { BindProbeEvidence } from "@/lib/types/api";

/**
 * v1.1-D task Sec5/Sec32 (CRITICAL, verified in
 * components/status/bind-probe-badge.test.tsx): `verified_free` means
 * "PortForge verified this binding was free on the target host at probe
 * time." It is NEVER a guarantee the port is currently free -- an
 * unmanaged process on that host can still bind it afterward (the v1.1-B
 * TOCTOU race, proven physically -- see docs/v1.1/v1.1-b-implementation.md
 * Sec11). Every label/description here is written to make that
 * unmistakable; do not change this wording to sound more certain.
 */
interface BindProbeMeta {
  label: string;
  icon: LucideIcon;
  className: string;
  /** Full sentence, safe to show as a visible caption -- never ONLY in a
   * tooltip (task Sec19: tooltips must not hold essential information
   * unavailable elsewhere). */
  description: string;
}

const BIND_PROBE_META: Record<BindProbeEvidence, BindProbeMeta> = {
  verified_free: {
    label: "Verified free",
    icon: CircleCheck,
    className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    description:
      "Verified free on the target host at probe time. This is not a guarantee the port is currently free -- an unmanaged process on that host can still bind it afterward.",
  },
  verified_occupied: {
    label: "Verified occupied",
    icon: AlertTriangle,
    className: "bg-red-500/10 text-red-400 border-red-500/20",
    description: "Verified occupied on the target host at probe time.",
  },
  expired: {
    label: "Evidence expired",
    icon: Clock,
    className: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    description:
      "A remote probe ran for this binding, but its result is now too old to trust and is not used as current evidence.",
  },
  unavailable: {
    label: "Probe unavailable",
    icon: CircleOff,
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    description: "The remote probe attempt itself failed, or the host could not be reached to answer it.",
  },
  not_remote_capable: {
    label: "Not remote-verified",
    icon: HelpCircle,
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    description:
      "No remote verification is available for this binding (a legacy agent that doesn't support remote probing, or this binding was never probed).",
  },
};

export function getBindProbeMeta(value: BindProbeEvidence): BindProbeMeta {
  return BIND_PROBE_META[value] ?? BIND_PROBE_META.not_remote_capable;
}

export function BindProbeBadge({ value, className }: { value: BindProbeEvidence; className?: string }) {
  const meta = getBindProbeMeta(value);
  const Icon = meta.icon;

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
              meta.className,
              className,
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {meta.label}
          </span>
        }
      />
      <TooltipContent>{meta.description}</TooltipContent>
    </Tooltip>
  );
}
