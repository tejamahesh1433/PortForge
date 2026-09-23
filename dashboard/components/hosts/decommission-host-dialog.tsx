"use client";

import { useState } from "react";
import { PowerOff } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/toast";
import { decommissionHost } from "@/lib/api/resources";
import { getHostHealthState } from "@/lib/utils/host-health";
import { hostnameConfirmationMatches } from "@/components/hosts/remove-host-dialog";
import type { HostOut } from "@/lib/types/api";

export function DecommissionHostDialog({ host }: { host: HostOut }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const queryClient = useQueryClient();

  const health = getHostHealthState(host);
  const isHealthyOrRecent =
    health === "HEALTHY" || (typeof host.age_seconds === "number" && host.age_seconds < 120);

  const confirmed = hostnameConfirmationMatches(host.hostname, typed);

  const mutation = useMutation({
    mutationFn: () => decommissionHost(host.id, reason.trim() ? { reason: reason.trim() } : {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hosts"] });
      void queryClient.invalidateQueries({ queryKey: ["host", host.id] });
      toast.add({
        type: "success",
        title: "Host decommissioned",
        description: `${host.hostname} has been marked as decommissioned. The remote agent was not stopped.`,
      });
      setOpen(false);
      setTyped("");
      setReason("");
    },
    onError: (error: Error) => {
      toast.add({
        type: "error",
        title: "Could not decommission host",
        description: error.message,
      });
    },
  });

  const openDialog = () => {
    setTyped("");
    setReason("");
    setOpen(true);
  };

  const cancel = () => {
    if (mutation.isPending) return;
    setOpen(false);
    setTyped("");
    setReason("");
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={openDialog}>
        <PowerOff className="size-4" />
        Decommission
      </Button>

      <Dialog open={open} onOpenChange={(next) => (next ? openDialog() : cancel())}>
        <DialogContent className="max-w-lg sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Decommission host</DialogTitle>
            <DialogDescription>
              Marks this host as decommissioned. The Central record and decommission tombstone are
              retained so this UUID cannot be re-enrolled accidentally.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-border bg-muted/30 p-3 text-muted-foreground space-y-1.5">
              <p>
                <span className="font-medium text-foreground">Credential invalidated.</span>{" "}
                The existing agent credential stops working immediately. The agent will no longer
                be able to report to Central.
              </p>
              <p>
                <span className="font-medium text-foreground">Tombstone retained.</span>{" "}
                The host record is kept with a DECOMMISSIONED marker so this UUID cannot be
                re-enrolled under a new enrollment token through ordinary means.
              </p>
              <p>
                <span className="font-medium text-foreground">Remote agent NOT stopped.</span>{" "}
                Decommissioning does not stop or uninstall the PortForge agent on the remote
                machine. You must do that separately.
              </p>
              <p>
                <span className="font-medium text-foreground">Re-enrollment rejected.</span>{" "}
                Ordinary re-enrollment with a new token will be rejected while this tombstone
                exists. Use Reactivate to allow enrollment again.
              </p>
            </div>

            {isHealthyOrRecent ? (
              <div
                role="status"
                className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-100"
              >
                <p className="font-medium">This host appears to still be running.</p>
                <p className="mt-1 text-amber-100/80">
                  Decommissioning a recently active host will cut off its agent immediately.
                  Make sure the machine is intentionally being retired before continuing.
                </p>
              </div>
            ) : null}

            <div className="space-y-2">
              <label htmlFor="decommission-reason" className="text-xs font-medium text-foreground">
                Reason (optional)
              </label>
              <Input
                id="decommission-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. Hardware retired, server repurposed"
                autoComplete="off"
                disabled={mutation.isPending}
              />
            </div>

            <div className="space-y-2">
              <label
                htmlFor="decommission-host-confirm"
                className="text-xs font-medium text-foreground"
              >
                Type the exact hostname to confirm (case-sensitive)
              </label>
              <Input
                id="decommission-host-confirm"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={host.hostname}
                autoComplete="off"
                spellCheck={false}
                disabled={mutation.isPending}
              />
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={cancel} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={!confirmed || mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? "Decommissioning…" : "Decommission host"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
