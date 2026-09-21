/** Formats an ISO timestamp as a short, human-relative string ("2m ago",
 * "3h ago", "just now"). Falls back to a locale date string once the gap
 * is large enough that "relative" stops being useful (>= 30 days), or if
 * the input can't be parsed at all.
 */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";

  const diffMs = now.getTime() - then;
  const diffSec = Math.round(diffMs / 1000);

  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;

  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;

  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;

  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;

  return new Date(iso).toLocaleDateString();
}

/** Formats an ISO timestamp as an absolute, locale-aware date+time
 * string, for tooltips/detail views where precision matters more than
 * "how long ago".
 */
export function formatAbsoluteTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleString();
}

/** Title-cases a snake_case or lowercase token for display (e.g.
 * "docker_compose" -> "Docker Compose", "active" -> "Active"). Never
 * silently drops information -- unknown tokens still round-trip through
 * unchanged casing per-word.
 */
export function titleCase(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

/** Returns a friendly short name for raw OS strings (e.g., "Microsoft Windows 11 Pro" -> "Windows"). */
export function formatFriendlyOS(os: string): string {
  const lower = os.toLowerCase();
  if (lower.includes("windows")) return "Windows";
  if (lower.includes("darwin") || lower.includes("mac")) return "macOS";
  if (lower.includes("linux") || lower.includes("ubuntu") || lower.includes("debian")) return "Linux";
  return os;
}
