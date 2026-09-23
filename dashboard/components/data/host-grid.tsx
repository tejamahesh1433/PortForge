import { Server } from "lucide-react";
import type { ReactNode } from "react";
import { EmptyState } from "@/components/feedback/empty-state";
import type { HostOut } from "@/lib/types/api";
import { HostCard } from "./host-card";

/** Responsive host inventory grid, used by both Overview (top hosts) and
 * the full Hosts page. Renders a polished empty state when Central
 * legitimately has zero enrolled hosts on file -- or, when `filtered` is
 * set (the Hosts page's search/filter controls narrowed a non-empty
 * inventory down to zero matches), a distinct message that doesn't
 * misleadingly imply Central has no hosts at all.
 */
export function HostGrid({
  hosts,
  filtered = false,
  emptyAction,
}: {
  hosts: HostOut[];
  filtered?: boolean;
  emptyAction?: ReactNode;
}) {
  if (hosts.length === 0) {
    return filtered ? (
      <EmptyState
        icon={Server}
        title="No hosts match your filters"
        description="Try a different search term or reset the filters above."
      />
    ) : (
      <EmptyState
        icon={Server}
        title="No hosts enrolled yet"
        description="Generate an enrollment token, run it on the new machine, and the host will appear here."
        action={emptyAction}
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {hosts.map((host) => (
        <HostCard key={host.id} host={host} />
      ))}
    </div>
  );
}
