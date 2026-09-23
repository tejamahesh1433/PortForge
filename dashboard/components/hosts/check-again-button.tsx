"use client";

import { useState, type ComponentProps, type MouseEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";
import { queryKeys } from "@/hooks/query-keys";
import { getHost, listHosts } from "@/lib/api/resources";
import { getHostHealthState } from "@/lib/utils/host-health";
import { cn } from "cn";

type ButtonProps = ComponentProps<typeof Button>;

/**
 * Recheck host status from Central.
 *
 * Central cannot remotely wake an agent — this only refreshes last-seen /
 * health_state. If the agent has recovered, status flips to healthy; if not,
 * the operator gets a clear "still stale/offline" result.
 */
export function CheckAgainButton({
  hostId,
  hostname,
  className,
  variant = "outline",
  size = "sm",
  stopPropagation = false,
}: {
  hostId: string;
  hostname: string;
  className?: string;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
  /** Use inside clickable cards so Recheck status does not navigate. */
  stopPropagation?: boolean;
}) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);

  const onClick = async (event: MouseEvent) => {
    if (stopPropagation) {
      event.preventDefault();
      event.stopPropagation();
    }
    setPending(true);
    try {
      const host = await queryClient.fetchQuery({
        queryKey: queryKeys.hosts.detail(hostId),
        queryFn: ({ signal }) => getHost(hostId, signal),
      });
      await queryClient.invalidateQueries({ queryKey: ["hosts"] });
      const state = getHostHealthState(host);
      if (state === "HEALTHY") {
        toast.add({
          type: "success",
          title: `${hostname} is healthy`,
          description: "Central has a fresh heartbeat from this agent.",
        });
      } else {
        toast.add({
          type: "error",
          title: `${hostname} is still ${state.toLowerCase()}`,
          description:
            "Central has not received a recent heartbeat. Confirm the agent is running on that machine and can reach Central, then recheck status.",
        });
      }
    } catch (error) {
      toast.add({
        type: "error",
        title: "Could not recheck status",
        description: error instanceof Error ? error.message : "Unknown error",
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      className={cn(className)}
      disabled={pending}
      title="Refresh the latest host status from PortForge Central."
      onClick={(event) => void onClick(event)}
    >
      <RefreshCw className={cn("size-3.5", pending && "animate-spin")} aria-hidden="true" />
      {pending ? "Rechecking…" : "Recheck status"}
    </Button>
  );
}

/** Fleet-wide: refresh every host and report how many are still unhealthy. */
export function CheckFleetAgainButton({
  className,
  variant = "outline",
  size = "sm",
}: {
  className?: string;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
}) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);

  const onClick = async () => {
    setPending(true);
    try {
      const page = await queryClient.fetchQuery({
        queryKey: queryKeys.hosts.list({ limit: 100, offset: 0 }),
        queryFn: ({ signal }) => listHosts({ limit: 100, offset: 0 }, signal),
      });
      await queryClient.invalidateQueries({ queryKey: ["hosts"] });
      const unhealthy = page.items.filter((h) => getHostHealthState(h) !== "HEALTHY");
      if (unhealthy.length === 0) {
        toast.add({
          type: "success",
          title: "All hosts healthy",
          description: `Checked ${page.items.length} host${page.items.length === 1 ? "" : "s"}.`,
        });
      } else {
        const names = unhealthy
          .map((h) => h.hostname)
          .slice(0, 4)
          .join(", ");
        const more = unhealthy.length > 4 ? ` (+${unhealthy.length - 4} more)` : "";
        toast.add({
          type: "error",
          title: `${unhealthy.length} host${unhealthy.length === 1 ? "" : "s"} still stale or offline`,
          description: `${names}${more}. Agents must reconnect to Central — recheck status after fixing them.`,
        });
      }
    } catch (error) {
      toast.add({
        type: "error",
        title: "Could not recheck status",
        description: error instanceof Error ? error.message : "Unknown error",
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      className={cn(className)}
      disabled={pending}
      title="Refresh the latest host status from PortForge Central."
      onClick={() => void onClick()}
    >
      <RefreshCw className={cn("size-3.5", pending && "animate-spin")} aria-hidden="true" />
      {pending ? "Rechecking…" : "Recheck status"}
    </Button>
  );
}
