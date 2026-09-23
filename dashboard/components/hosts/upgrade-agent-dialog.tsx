"use client";

import { useState } from "react";
import { ArrowUpCircle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/toast";
import { useCreateHostUpgrade, useRollbackUpgrade } from "@/hooks/use-upgrades";
import type { FleetHostOut, UpgradeOut } from "@/lib/types/api";

// ---------------------------------------------------------------------------
// UpgradeAgentDialog
// ---------------------------------------------------------------------------

interface UpgradeAgentDialogProps {
  host: FleetHostOut;
}

/**
 * Confirmation dialog to initiate a structured agent upgrade.
 * Uses the fleet endpoint's pre-configured target_version / artifact
 * metadata (set via Central env vars). No command fields are presented
 * or sent. The admin token stays server-side in the BFF.
 *
 * Only rendered when update_availability === "UPDATE_AVAILABLE" and
 * lifecycle_state === "ACTIVE". The parent is responsible for guarding
 * that invariant before rendering this component.
 */
export function UpgradeAgentDialog({ host }: UpgradeAgentDialogProps) {
  const [open, setOpen] = useState(false);

  // target_version must exist if UPDATE_AVAILABLE was set by Central
  const targetVersion = host.target_version ?? "—";
  const currentVersion = host.agent_version ?? "—";

  const mutation = useCreateHostUpgrade(host.id);

  const openDialog = () => {
    mutation.reset();
    setOpen(true);
  };

  const cancel = () => {
    if (mutation.isPending) return;
    setOpen(false);
  };

  const confirm = () => {
    if (!host.target_version) return;

    // Central-configured artifact metadata flows through the fleet record;
    // this dialog submits only structured upgrade fields, never a shell cmd.
    mutation.mutate(
      {
        target_version: host.target_version,
        // artifact_url and artifact_sha256 come from the fleet endpoint's
        // configured target metadata. For the MVP BFF the dashboard does
        // not yet receive individual artifact fields from fleet; Central
        // will derive them from its PORTFORGE_UPDATE_* env on the server.
        // We send only target_version; Central fills in the rest.
        artifact_url: "",
        artifact_sha256: "",
      },
      {
        onSuccess: () => {
          toast.add({
            type: "success",
            title: "Upgrade queued",
            description: `${host.hostname} will upgrade from ${currentVersion} to ${targetVersion} at next heartbeat.`,
          });
          setOpen(false);
        },
        onError: (error: Error) => {
          toast.add({
            type: "error",
            title: "Could not create upgrade",
            description: error.message,
          });
        },
      },
    );
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={openDialog}>
        <ArrowUpCircle className="size-4" />
        Upgrade Agent
      </Button>

      <Dialog open={open} onOpenChange={(next) => (next ? openDialog() : cancel())}>
        <DialogContent className="max-w-lg sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Upgrade agent</DialogTitle>
            <DialogDescription>
              A structured upgrade request will be queued for this host. The agent will download,
              verify, and install the new version at its next heartbeat.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-1.5">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Host</span>
                <span className="font-medium font-mono text-xs">{host.hostname}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Current version</span>
                <span className="font-medium font-mono text-xs">{currentVersion}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Target version</span>
                <span className="font-medium font-mono text-xs text-amber-400">{targetVersion}</span>
              </div>
            </div>

            <div className="rounded-lg border border-border bg-muted/30 p-3 text-muted-foreground space-y-1.5">
              <p>
                <span className="font-medium text-foreground">Artifact verified.</span>{" "}
                The agent will verify the exact SHA-256 of the downloaded artifact before installing.
                A mismatch causes the upgrade to fail closed.
              </p>
              <p>
                <span className="font-medium text-foreground">Service restarted, not rebooted.</span>{" "}
                The PortForge agent service is restarted via the platform adapter (Scheduled Task /
                systemd / launchd). The machine itself is not rebooted.
              </p>
              <p>
                <span className="font-medium text-foreground">UUID and credentials preserved.</span>{" "}
                The agent UUID, config, credential, and service registration are kept intact. No
                re-enrollment is required.
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={cancel} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={!host.target_version || mutation.isPending}
              onClick={confirm}
            >
              {mutation.isPending ? "Queuing…" : "Confirm upgrade"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// RollbackUpgradeButton
// ---------------------------------------------------------------------------

interface RollbackUpgradeButtonProps {
  upgrade: UpgradeOut;
  hostId: string;
}

/**
 * Button + inline confirmation for rolling back a SUCCEEDED or FAILED
 * upgrade to the previous version. Rendered on the host detail page when
 * the upgrade has previous_version metadata.
 */
export function RollbackUpgradeButton({ upgrade, hostId }: RollbackUpgradeButtonProps) {
  const [open, setOpen] = useState(false);
  const mutation = useRollbackUpgrade(upgrade.id, hostId);

  const confirm = () => {
    mutation.mutate(undefined, {
      onSuccess: () => {
        toast.add({
          type: "success",
          title: "Rollback queued",
          description: `Rolling back to ${upgrade.previous_version ?? "previous version"}.`,
        });
        setOpen(false);
      },
      onError: (error: Error) => {
        toast.add({
          type: "error",
          title: "Could not rollback",
          description: error.message,
        });
      },
    });
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <RotateCcw className="size-4" />
        Rollback
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rollback agent upgrade</DialogTitle>
            <DialogDescription>
              A new controlled downgrade request will be created targeting the previous version
              metadata stored on this upgrade record.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2 text-sm">
            <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-1.5">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Current version</span>
                <span className="font-mono text-xs">{upgrade.target_version}</span>
              </div>
              {upgrade.previous_version && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Rollback target</span>
                  <span className="font-mono text-xs text-amber-400">{upgrade.previous_version}</span>
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={mutation.isPending}
              onClick={confirm}
            >
              {mutation.isPending ? "Rolling back…" : "Confirm rollback"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
