import {
  AlertTriangle,
  Box,
  Cable,
  CircleCheck,
  CircleOff,
  Cog,
  Container,
  Lock,
  type LucideIcon,
} from "lucide-react";

/**
 * Central, single source of truth for every status/state/source badge's
 * label, icon, and color -- StatusBadge/HostStatus/DockerStatus/
 * PortStateBadge/PortSourceBadge all read from here rather than each
 * re-implementing their own color/label mapping. Never color-only: every
 * entry pairs an icon + text label with its color (see StatusBadge).
 */
export interface StatusMeta {
  label: string;
  icon: LucideIcon;
  /** Combined badge classes (bg + text + border), dark-theme-first. */
  className: string;
  /** Icon/text-only color class, for compact contexts (dense table cells). */
  textClassName: string;
  /** Dot/glyph fill color, for pulse indicators. */
  dotClassName: string;
}

const META = {
  // Host liveness (derived -- see lib/utils/host-status.ts)
  online: {
    label: "Online",
    icon: CircleCheck,
    className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    textClassName: "text-emerald-400",
    dotClassName: "bg-emerald-400",
  },
  offline: {
    label: "Offline",
    icon: CircleOff,
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    textClassName: "text-zinc-400",
    dotClassName: "bg-zinc-500",
  },  healthy: {
    label: "Healthy",
    icon: CircleCheck,
    className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    textClassName: "text-emerald-400",
    dotClassName: "bg-emerald-400",
  },
  stale: {
    label: "Stale",
    icon: AlertTriangle,
    className: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    textClassName: "text-amber-400",
    dotClassName: "bg-amber-400",
  },
  degraded: {
    label: "Degraded",
    icon: AlertTriangle,
    className: "bg-red-500/10 text-red-400 border-red-500/20",
    textClassName: "text-red-400",
    dotClassName: "bg-red-400",
  },
  // Port lifecycle state (agent/portforge_agent/models.py:PortState)
  active: {
    label: "Active",
    icon: CircleCheck,
    className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    textClassName: "text-emerald-400",
    dotClassName: "bg-emerald-400",
  },
  free: {
    label: "Free",
    icon: Cable,
    className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    textClassName: "text-zinc-400",
    dotClassName: "bg-zinc-500",
  },
  reserved: {
    label: "Reserved",
    icon: Lock,
    className: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    textClassName: "text-blue-400",
    dotClassName: "bg-blue-400",
  },
  conflict: {
    label: "Conflict",
    icon: AlertTriangle,
    className: "bg-red-500/10 text-red-400 border-red-500/20",
    textClassName: "text-red-400",
    dotClassName: "bg-red-400",
  },
  system: {
    label: "System",
    icon: Cog,
    className: "bg-violet-500/10 text-violet-400 border-violet-500/20",
    textClassName: "text-violet-400",
    dotClassName: "bg-violet-400",
  },
  // Observation source (agent/portforge_agent/models.py:Source)
  docker: {
    label: "Docker",
    icon: Container,
    className: "bg-sky-500/10 text-sky-400 border-sky-500/20",
    textClassName: "text-sky-400",
    dotClassName: "bg-sky-400",
  },
  process: {
    label: "Process",
    icon: Box,
    className: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    textClassName: "text-amber-400",
    dotClassName: "bg-amber-400",
  },
} as const satisfies Record<string, StatusMeta>;

export type KnownStatusKey = keyof typeof META;

const FALLBACK: StatusMeta = {
  label: "Unknown",
  icon: Cable,
  className: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
  textClassName: "text-zinc-400",
  dotClassName: "bg-zinc-500",
};

/** Looks up display metadata for any raw backend status/state/source
 * string (case-insensitive, e.g. "ACTIVE" or "active"). Never throws --
 * an unrecognized value degrades to a neutral fallback badge showing the
 * raw string as its label, so a future backend value never renders blank.
 */
export function getStatusMeta(rawValue: string): StatusMeta {
  const key = rawValue.toLowerCase() as KnownStatusKey;
  const known = META[key];
  if (known) return known;
  return { ...FALLBACK, label: rawValue };
}
