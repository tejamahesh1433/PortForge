"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { reactivateHost } from "@/lib/api/resources";
import type { HostOut } from "@/lib/types/api";

export function ReactivateHostDialog({ host }: { host: HostOut }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => reactivateHost(host.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hosts"] });
      void queryClient.invalidateQueries({ queryKey: ["host", host.id] });
      toast.add({
        type: "success",
        title: "Host reactivated",
        description: `${host.hostname} is now active. A new enrollment is required before the agent can report again.`,
      });
      setOpen(false);
    },
    onError: (error: Error) => {
      toast.add({
        type: "error",
        title: "Could not reactivate host",
        description: error.message,
      });
    },
  });

  const cancel = () => {
    if (mutation.isPending) return;
    setOpen(false);
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <RefreshCw className="size-4" />
        Reactivate
      </Button>

      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : cancel())}>
        <DialogContent className="max-w-lg sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Reactivate host</DialogTitle>
            <DialogDescription>
              Removes the decommission tombstone and marks this host as active again.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-border bg-muted/30 p-3 text-muted-foreground space-y-1.5">
              <p>
                <span className="font-medium text-foreground">Enrollment allowed again.</span>{" "}
                Once reactivated, a machine with this UUID can be enrolled using a valid enrollment
                token.
              </p>
              <p>
                <span className="font-medium text-foreground">Old credential stays invalid.</span>{" "}
                The credential that existed before decommissioning is not restored. A fresh
                enrollment with a new enrollment token is required before the agent can report to
                Central.
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={cancel} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? "Reactivating…" : "Reactivate host"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
