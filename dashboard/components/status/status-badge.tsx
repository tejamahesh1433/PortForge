import { cn } from "cn";
import { getStatusMeta } from "@/lib/utils/status-meta";

interface StatusBadgeProps {
  /** Raw backend status/state/source string (e.g. "ACTIVE", "docker"). */
  value: string;
  className?: string;
  /** Icon-only + color, no text label -- for dense table cells where a
   * neighboring text column already states the value.
   */
  compact?: boolean;
}

/** The one shared building block for every status/state indicator in the
 * app (host liveness, port state, observation source). Never color-only:
 * always icon + text unless `compact` is explicitly requested, and even
 * then the color is paired with a distinct icon shape per state.
 */
export function StatusBadge({ value, className, compact = false }: StatusBadgeProps) {
  const meta = getStatusMeta(value);
  const Icon = meta.icon;

  if (compact) {
    return (
      <span
        className={cn("inline-flex items-center gap-1.5", className)}
        title={meta.label}
      >
        <Icon className={cn("size-3.5", meta.textClassName)} aria-hidden="true" />
        <span className="sr-only">{meta.label}</span>
      </span>
    );
  }

  return (
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
  );
}
