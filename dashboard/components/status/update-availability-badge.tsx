import { cn } from "cn";
import { ArrowUpCircle, CheckCircle2, CircleAlert, HelpCircle } from "lucide-react";
import type { UpdateAvailability } from "@/lib/types/api";

interface UpdateAvailabilityBadgeProps {
  availability: UpdateAvailability;
  className?: string;
}

const CONFIG: Record<
  UpdateAvailability,
  { icon: React.ElementType; label: string; classes: string }
> = {
  CURRENT: {
    icon: CheckCircle2,
    label: "Current",
    classes: "border-emerald-500/20 bg-emerald-500/10 text-emerald-400",
  },
  UPDATE_AVAILABLE: {
    icon: ArrowUpCircle,
    label: "Update available",
    classes: "border-amber-500/30 bg-amber-500/10 text-amber-400",
  },
  UNSUPPORTED: {
    icon: CircleAlert,
    label: "Unsupported",
    classes: "border-red-500/30 bg-red-500/10 text-red-400",
  },
  UNKNOWN: {
    icon: HelpCircle,
    label: "Unknown",
    classes: "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
  },
};

export function UpdateAvailabilityBadge({ availability, className }: UpdateAvailabilityBadgeProps) {
  const { icon: Icon, label, classes } = CONFIG[availability] ?? CONFIG.UNKNOWN;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
        classes,
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {label}
    </span>
  );
}
