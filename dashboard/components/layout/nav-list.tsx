"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { NavGroup, NavItem } from "@/lib/nav-config";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

interface NavListProps {
  items: NavItem[];
  /** Collapsed = icon-only with a hover tooltip (desktop sidebar). */
  collapsed?: boolean;
  onNavigate?: () => void;
}

export function NavList({ items, collapsed = false, onNavigate }: NavListProps) {
  const pathname = usePathname();

  return (
    <ul className="flex flex-col gap-0.5" role="list">
      {items.map((item) => {
        const active = isActive(pathname, item.href);
        const Icon = item.icon;

        const link = (
          <Link
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "group flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium outline-none transition-colors",
              "focus-visible:ring-2 focus-visible:ring-ring/50",
              collapsed && "justify-center px-0",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon
              className={cn("size-4 shrink-0", active && "text-primary")}
              aria-hidden="true"
            />
            {!collapsed && <span className="truncate">{item.label}</span>}
          </Link>
        );

        return (
          <li key={item.href}>
            {collapsed ? (
              <Tooltip>
                <TooltipTrigger render={link} />
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            ) : (
              link
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function NavGroups({ groups, collapsed = false, onNavigate }: { groups: NavGroup[]; collapsed?: boolean; onNavigate?: () => void }) {
  return <div className="space-y-4">{groups.map((group, index) => <div key={group.label ?? index}>{group.label && !collapsed ? <p className="mb-1 px-2.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground/70">{group.label}</p> : null}<NavList items={group.items} collapsed={collapsed} onNavigate={onNavigate} /></div>)}</div>;
}
