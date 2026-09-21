"use client";

import Link from "next/link";
import { ChevronsLeft, ChevronsRight, Network } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useSidebarState } from "@/hooks/use-sidebar-state";
import { NAV_FOOTER_ITEMS, NAV_GROUPS } from "@/lib/nav-config";
import { NavGroups, NavList } from "./nav-list";

/**
 * Persistent desktop sidebar (hidden below `md`; see MobileNav for the
 * small-screen equivalent). Collapse state persists across reloads via
 * useSidebarState. Width transitions with a CSS transition rather than a
 * layout-jumping toggle.
 */
export function Sidebar() {
  const { collapsed, toggle, hydrated } = useSidebarState();

  return (
    <aside
      className={cn(
        "hidden md:flex md:flex-col md:border-r md:border-border md:bg-card",
        "h-svh shrink-0 transition-[width] duration-200 ease-in-out",
        collapsed ? "md:w-16" : "md:w-60",
        !hydrated && "duration-0",
      )}
      aria-label="Primary"
    >
      <div className={cn("flex h-14 items-center gap-2 px-4", collapsed && "justify-center px-0")}>
        <Link
          href="/"
          className="flex items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary">
            <Network className="size-4" aria-hidden="true" />
          </span>
          {!collapsed && (
            <span className="text-sm font-semibold tracking-tight text-foreground">PortForge</span>
          )}
        </Link>
      </div>

      <Separator />

      <nav className="flex flex-1 flex-col justify-between overflow-y-auto px-2 py-3">
        <NavGroups groups={NAV_GROUPS} collapsed={collapsed} />
        <div className="flex flex-col gap-2">
          <Separator />
          <NavList items={NAV_FOOTER_ITEMS} collapsed={collapsed} />
        </div>
      </nav>

      <Separator />

      <div className={cn("flex p-2", collapsed && "justify-center")}>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-pressed={collapsed}
        >
          {collapsed ? (
            <ChevronsRight className="size-4" aria-hidden="true" />
          ) : (
            <ChevronsLeft className="size-4" aria-hidden="true" />
          )}
        </Button>
      </div>
    </aside>
  );
}

