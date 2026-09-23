"use client";

import { useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
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
import { useAllocations } from "@/hooks/use-allocations";
import { useReservations } from "@/hooks/use-reservations";
import { deleteHost } from "@/lib/api/resources";
import { getHostHealthState } from "@/lib/utils/host-health";
import type { HostOut } from "@/lib/types/api";

/** Exact hostname match after trimming ends; case-sensitive. */
export function hostnameConfirmationMatches(expected: string, typed: string): boolean {
  return typed.trim() === expected.trim();
}

export function RemoveHostDialog({ host }: { host: HostOut }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const router = useRouter();
  const queryClient = useQueryClient();

  const allocations = useAllocations({ host_id: host.id, limit: 100 });
  const reservations = useReservations({ host_id: host.id, limit: 100 });

  const activeAllocationCount = useMemo(
    () => (allocations.data?.items ?? []).filter((a) => a.status === "active").length,
    [allocations.data],
  );
  const reservationCount = reservations.data?.total ?? reservations.data?.items?.length ?? 0;

  const health = getHostHealthState(host);
  const appearsRunning =
    health === "HEALTHY" || (typeof host.age_seconds === "number" && host.age_seconds < 120);

  const confirmed = hostnameConfirmationMatches(host.hostname, typed);

  const mutation = useMutation({
    mutationFn: () => deleteHost(host.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hosts"] });
      void queryClient.invalidateQueries({ queryKey: ["allocations"] });
      void queryClient.invalidateQueries({ queryKey: ["reservations"] });
      toast.add({
        type: "success",
        title: "Host record removed",
        description: `${host.hostname} is no longer tracked by Central. The remote agent was not stopped.`,
      });
      setOpen(false);
      setTyped("");
      router.push("/hosts");
    },
    onError: (error: Error) => {
      toast.add({
        type: "error",
        title: "Could not remove host record",
        description: error.message,
      });
    },
  });

  const openDialog = () => {
    setTyped("");
    setOpen(true);
  };

  const cancel = () => {
    if (mutation.isPending) return;
    setOpen(false);
    setTyped("");
  };

  return (
    <>
      <Button variant="destructive" size="sm" onClick={openDialog}>
        <Trash2 className="size-4" />
        Remove record
      </Button>

      <Dialog open={open} onOpenChange={(next) => (next ? openDialog() : cancel())}>
        <DialogContent className="max-w-lg sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Remove record</DialogTitle>
            <DialogDescription>
              Deletes this host and its associated data from PortForge Central. It does not stop or
              uninstall the PortForge agent on the remote machine.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-border bg-muted/30 p-3 text-muted-foreground">
              Removing the Central record for <span className="font-medium text-foreground">{host.hostname}</span>{" "}
              removes Central-side ports, reservations, allocations, credentials, and related history.
              If the agent is still running, its existing credential will stop working. To reconnect
              later, re-enroll the machine with a new enrollment token.
            </div>

            {host.lifecycle_state === "DECOMMISSIONED" ? (
              <div
                role="status"
                className="rounded-lg border border-zinc-500/30 bg-zinc-500/10 p-3 text-zinc-300"
              >
                Removing this record also removes the decommission tombstone. A machine with this
                UUID may be enrolled again later.
              </div>
            ) : null}

            {appearsRunning ? (
              <div
                role="status"
                className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-100"
              >
                <p className="font-medium">This host appears to still be running.</p>
                <p className="mt-1 text-amber-100/80">
                  Removing its Central record does not stop the remote PortForge agent.
                </p>
              </div>
            ) : null}

            {(allocations.isSuccess || reservations.isSuccess) &&
            (activeAllocationCount > 0 || reservationCount > 0) ? (
              <div
                role="status"
                className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-100"
              >
                This host has {activeAllocationCount} active allocation
                {activeAllocationCount === 1 ? "" : "s"} and {reservationCount} reservation
                {reservationCount === 1 ? "" : "s"}. Removing the record will remove these Central
                records.
              </div>
            ) : allocations.isSuccess && reservations.isSuccess ? null : (
              <div className="rounded-lg border border-border p-3 text-muted-foreground">
                Associated allocations and reservations on Central are removed with this host record.
              </div>
            )}

            <div className="space-y-2">
              <label htmlFor="remove-host-confirm" className="text-xs font-medium text-foreground">
                Type the exact hostname to confirm (case-sensitive)
              </label>
              <Input
                id="remove-host-confirm"
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
              {mutation.isPending ? "Removing…" : "Remove record"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
