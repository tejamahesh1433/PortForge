import {
  AlertTriangle,
  Folder,
  Gauge,
  Lightbulb,
  Lock,
  Network,
  Server,
  Settings,
  Activity,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Short description shown as a collapsed-sidebar tooltip. */
  description: string;
}

export interface NavGroup {
  label?: string;
  items: NavItem[];
}

/** Primary navigation, top to bottom in the sidebar. Settings is kept
 * separate (see NAV_FOOTER_ITEMS) so it always renders pinned to the
 * bottom, per the sidebar spec.
 */
export const NAV_MAIN_ITEMS: NavItem[] = [
  { href: "/", label: "Overview", icon: Gauge, description: "Fleet-wide summary" },
  { href: "/hosts", label: "Hosts", icon: Server, description: "Enrolled machines" },
  { href: "/ports", label: "Ports", icon: Network, description: "Every known binding" },
  { href: "/projects", label: "Projects", icon: Folder, description: "Grouped by project" },
  { href: "/reservations", label: "Reservations", icon: Lock, description: "Claimed ports" },
  { href: "/conflicts", label: "Conflicts", icon: AlertTriangle, description: "Ownership mismatches" },
  {
    href: "/recommendations",
    label: "Recommendations",
    icon: Lightbulb,
    description: "Suggested free ports",
  },
  { href: "/activity", label: "Activity", icon: Activity, description: "Operational timeline" },
  { href: "/diagnostics", label: "Diagnostics", icon: Activity, description: "System health" },
];

export const NAV_GROUPS: NavGroup[] = [
  { items: NAV_MAIN_ITEMS.slice(0, 1) },
  { label: "Infrastructure", items: NAV_MAIN_ITEMS.slice(1, 4) },
  { label: "Operations", items: NAV_MAIN_ITEMS.slice(4, 7) },
  { label: "Observability", items: NAV_MAIN_ITEMS.slice(7, 9) },
];

export const NAV_FOOTER_ITEMS: NavItem[] = [
  { href: "/settings", label: "Settings", icon: Settings, description: "Central connection" },
];

export const ALL_NAV_ITEMS: NavItem[] = [...NAV_MAIN_ITEMS, ...NAV_FOOTER_ITEMS];

/** Resolves the page title for the TopBar from the current pathname.
 * Handles the dynamic /hosts/[hostId] route by falling back to its
 * parent's label ("Hosts") when no exact match is found.
 */
export function resolvePageTitle(pathname: string): string {
  const exact = ALL_NAV_ITEMS.find((item) => item.href === pathname);
  if (exact) return exact.label;

  const parent = ALL_NAV_ITEMS.find(
    (item) => item.href !== "/" && pathname.startsWith(`${item.href}/`),
  );
  return parent?.label ?? "PortForge";
}

