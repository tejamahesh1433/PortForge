"use client";

import Link from "next/link";
import { Cpu, Server } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { CheckAgainButton } from "@/components/hosts/check-again-button";
import { DockerStatus } from "@/components/status/docker-status";
import { HostStatus } from "@/components/status/host-status";
import { formatAbsoluteTime, formatRelativeTime, formatFriendlyOS } from "@/lib/utils/format";
import { getHostHealthState } from "@/lib/utils/host-health";
import type { HostOut } from "@/lib/types/api";

/** One enrolled host, summarized for the Overview grid / Hosts inventory.
 * Clicking (or Enter/Space while focused) navigates to /hosts/[hostId] --
 * the whole card is a real link, not a div with an onClick handler, so
 * it's keyboard/screen-reader reachable for free.
 */
export function HostCard({ host }: { host: HostOut }) {
  const health = getHostHealthState(host);
  const needsRetry = health === "STALE" || health === "OFFLINE";

  return (
    <Link
      href={`/hosts/${host.id}`}
      className="block rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
    >
      <Card className="h-full border-border bg-card transition-colors hover:border-primary/40">
        <CardHeader className="flex-row items-start justify-between gap-2 space-y-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Server className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">{host.hostname}</p>
              <p
                className="truncate text-xs text-muted-foreground"
                title={`${host.operating_system}${host.os_version ? ` · ${host.os_version}` : ""}`}
              >
                {formatFriendlyOS(host.operating_system)}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <HostStatus host={host} />
          {needsRetry ? (
            <CheckAgainButton
              hostId={host.id}
              hostname={host.hostname}
              stopPropagation
              size="sm"
              className="w-full"
            />
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            <DockerStatus available={host.docker_available} />
            {host.architecture && (
              <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
                <Cpu className="size-3" aria-hidden="true" />
                {host.architecture}
              </span>
            )}
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 pt-1 text-[11px] text-muted-foreground">
            <dt>Agent</dt>
            <dd className="truncate text-right font-medium text-foreground">{host.agent_version ?? "—"}</dd>
            <dt>Last seen</dt>
            <dd
              className="truncate text-right font-medium text-foreground"
              title={formatAbsoluteTime(host.last_seen)}
            >
              {formatRelativeTime(host.last_seen)}
            </dd>
          </dl>
        </CardContent>
      </Card>
    </Link>
  );
}
