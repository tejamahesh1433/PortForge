import { CircleCheck, CircleOff, HelpCircle, AlertTriangle, type LucideIcon } from "lucide-react";
import { cn } from "cn";
import { Card, CardContent } from "@/components/ui/card";
import type { HostDiagnosticsOut, HostOut } from "@/lib/types/api";

/**
 * v1.1-D task Sec7: legacy agent missing protocol metadata must display
 * honestly as unknown/legacy, never as broken. Compatibility warnings are
 * purely advisory -- this component never blocks or disables anything.
 */
const COMPATIBILITY_META: Record<
  NonNullable<HostOut["protocol_compatibility"]>,
  { label: string; icon: LucideIcon; className: string; description: string }
> = {
  compatible: {
    label: "Compatible",
    icon: CircleCheck,
    className: "text-emerald-400",
    description: "This agent's protocol version matches Central's -- no known compatibility gap.",
  },
  warning: {
    label: "Version mismatch",
    icon: AlertTriangle,
    className: "text-amber-400",
    description:
      "This agent reported a different protocol version than Central. Advisory only -- sync, allocation, and reservation operations are never blocked by this.",
  },
  unknown: {
    label: "Unknown / legacy",
    icon: HelpCircle,
    className: "text-zinc-400",
    description:
      "Central has no protocol-version metadata for this agent -- either it predates v1.1-A, or it hasn't reported one since. Not an error.",
  },
};

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}

export function CompatibilityCard({ host }: { host: HostOut }) {
  const compatibility = host.protocol_compatibility ?? "unknown";
  const meta = COMPATIBILITY_META[compatibility];
  const Icon = meta.icon;

  return (
    <Card className="border-border bg-card">
      <CardContent className="pt-6 space-y-4">
        <h3 className="text-sm font-medium text-foreground">Protocol Compatibility</h3>
        <DetailRow label="Agent Version" value={host.agent_version ?? "—"} />
        <DetailRow label="Agent Protocol Version" value={host.protocol_version ?? "unknown"} />
        <DetailRow
          label="Compatibility"
          value={
            <span className={cn("inline-flex items-center gap-1.5", meta.className)}>
              <Icon className="size-3.5" aria-hidden="true" />
              {meta.label}
            </span>
          }
        />
        <p className="text-xs text-muted-foreground">{meta.description}</p>
      </CardContent>
    </Card>
  );
}

const PROBE_CAPABILITY_META: Record<
  NonNullable<HostDiagnosticsOut["probe_capability"]>,
  { label: string; icon: LucideIcon; className: string; description: string }
> = {
  supported: {
    label: "Supported",
    icon: CircleCheck,
    className: "text-emerald-400",
    description: "This host's agent has previously answered at least one remote bind probe (v1.1-B).",
  },
  unavailable_offline: {
    label: "Unavailable (host not healthy)",
    icon: CircleOff,
    className: "text-zinc-400",
    description: "This host is currently stale or offline -- a remote probe could not be delivered or answered right now.",
  },
  unknown: {
    label: "Unknown / not yet probed",
    icon: HelpCircle,
    className: "text-zinc-400",
    description:
      "Central has no probe history for this host -- it may simply never have been asked, or it may be a legacy agent. These two cases cannot be distinguished from data alone.",
  },
};

export function ProbeCapabilityCard({ capability }: { capability: NonNullable<HostDiagnosticsOut["probe_capability"]> }) {
  const meta = PROBE_CAPABILITY_META[capability];
  const Icon = meta.icon;

  return (
    <Card className="border-border bg-card">
      <CardContent className="pt-6 space-y-4">
        <h3 className="text-sm font-medium text-foreground">Remote Probe Capability</h3>
        <DetailRow
          label="Capability"
          value={
            <span className={cn("inline-flex items-center gap-1.5", meta.className)}>
              <Icon className="size-3.5" aria-hidden="true" />
              {meta.label}
            </span>
          }
        />
        <p className="text-xs text-muted-foreground">{meta.description}</p>
      </CardContent>
    </Card>
  );
}
