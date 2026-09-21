"use client";

import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { useRefreshAll } from "@/hooks/use-refresh-all";
import { resolvePageTitle } from "@/lib/nav-config";
import { ConnectionStatus } from "./connection-status";
import { GlobalSearch } from "./global-search";
import { MobileNav } from "./mobile-nav";

export function TopBar() {
  const pathname = usePathname();
  const title = resolvePageTitle(pathname);
  const { refreshAll, isRefreshing } = useRefreshAll();

  return (
    <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur supports-backdrop-filter:bg-background/75">
      <MobileNav />
      <h1 className="shrink-0 text-sm font-semibold text-foreground">{title}</h1>

      <div className="flex flex-1 items-center justify-end gap-3">
        <GlobalSearch />
        <ConnectionStatus />
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => void refreshAll()}
          disabled={isRefreshing}
          aria-label="Refresh data"
        >
          <RefreshCw className={cn("size-4", isRefreshing && "animate-spin")} aria-hidden="true" />
        </Button>
      </div>
    </header>
  );
}
