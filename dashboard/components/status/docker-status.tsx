import { Container, ContainerIcon } from "lucide-react";
import { cn } from "cn";

/** Docker CLI/daemon availability indicator for a host -- reflects
 * `HostOut.docker_available` exactly (the agent's own is_docker_available()
 * check, reported at enrollment/heartbeat time). Deliberately doesn't
 * distinguish "CLI missing" vs "daemon down" here: Central only ever
 * receives the single boolean the agent already resolved (see
 * agent/portforge_agent/collectors/docker.py) -- that finer distinction
 * lives in the agent's own local diagnostics, not this dashboard.
 */
export function DockerStatus({ available, className }: { available: boolean; className?: string }) {
  const Icon = available ? Container : ContainerIcon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
        available
          ? "border-sky-500/20 bg-sky-500/10 text-sky-400"
          : "border-zinc-500/20 bg-zinc-500/10 text-zinc-500",
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {available ? "Docker available" : "No Docker"}
    </span>
  );
}
