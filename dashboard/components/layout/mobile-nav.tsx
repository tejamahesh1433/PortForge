"use client";

import { Menu, Network } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { NAV_FOOTER_ITEMS, NAV_GROUPS } from "@/lib/nav-config";
import { NavGroups, NavList } from "./nav-list";

/** Small-screen navigation: a slide-out drawer from the TopBar's menu
 * button, since the persistent Sidebar hides below `md`.
 */
export function MobileNav() {
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation" />
        }
      >
        <Menu className="size-5" aria-hidden="true" />
      </SheetTrigger>
      <SheetContent side="left" className="flex w-72 flex-col p-0">
        <SheetHeader className="px-4 pt-4">
          <SheetTitle className="flex items-center gap-2 text-sm font-semibold">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary">
              <Network className="size-4" aria-hidden="true" />
            </span>
            PortForge
          </SheetTitle>
        </SheetHeader>
        <Separator />
        <nav className="flex flex-1 flex-col justify-between overflow-y-auto px-2 py-3">
          <NavGroups groups={NAV_GROUPS} onNavigate={() => setOpen(false)} />
          <div className="flex flex-col gap-2">
            <Separator />
            <NavList items={NAV_FOOTER_ITEMS} onNavigate={() => setOpen(false)} />
          </div>
        </nav>
      </SheetContent>
    </Sheet>
  );
}

