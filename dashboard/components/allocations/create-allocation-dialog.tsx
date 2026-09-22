"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useCreateAllocation } from "@/hooks/use-allocations";
import type { HostOut } from "@/lib/types/api";
import { toast } from "@/components/ui/toast";

export function CreateAllocationDialog({ hosts, onCreated }: { hosts: HostOut[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [project, setProject] = useState("");
  const [service, setService] = useState("api");
  const [hostId, setHostId] = useState("");
  const [protocol, setProtocol] = useState<"tcp" | "udp">("tcp");
  const [preferred, setPreferred] = useState("");
  const [range, setRange] = useState("");
  const mutation = useCreateAllocation();

  const submit = () => {
    if (!project.trim() || !hostId || !service.trim()) return;
    mutation.mutate({
      project: project.trim(),
      host_id: hostId,
      requests: [{ name: service.trim(), purpose: service.trim(), protocol, ...(preferred ? { preferred_port: Number(preferred) } : {}), ...(range ? { requested_range: range.trim() } : {}) }],
    }, {
      onSuccess: (allocation) => {
        toast.add({ type: "success", title: "Allocation created", description: allocation.allocations.map((entry) => entry.port + "/" + entry.protocol).join(", ") });
        setOpen(false);
        onCreated();
      },
      onError: (error) => toast.add({ type: "error", title: "Allocation failed", description: error instanceof Error ? error.message : "Central could not create the allocation." }),
    });
  };

  return (
    <>
      <Button onClick={() => setOpen(true)}><Plus className="size-4" /> Create allocation</Button>
      <Dialog open={open} onOpenChange={(next) => !mutation.isPending && setOpen(next)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create allocation</DialogTitle>
            <DialogDescription>Reserve one port bundle through Central. Configuration files are not changed by this action.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block text-xs font-medium">Project<Input value={project} onChange={(event) => setProject(event.target.value)} placeholder="project name" /></label>
            <label className="block text-xs font-medium">Service<Input value={service} onChange={(event) => setService(event.target.value)} placeholder="api" /></label>
            <label className="block text-xs font-medium">Host<select className="mt-1 h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm" value={hostId} onChange={(event) => setHostId(event.target.value)}><option value="">Select a host</option>{hosts.map((host) => <option key={host.id} value={host.id}>{host.display_name ?? host.hostname}</option>)}</select></label>
            <div className="grid grid-cols-2 gap-3"><label className="block text-xs font-medium">Protocol<select className="mt-1 h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm" value={protocol} onChange={(event) => setProtocol(event.target.value as "tcp" | "udp")}><option value="tcp">TCP</option><option value="udp">UDP</option></select></label><label className="block text-xs font-medium">Preferred port<Input type="number" min={1} max={65535} value={preferred} onChange={(event) => setPreferred(event.target.value)} placeholder="optional" /></label></div>
            <label className="block text-xs font-medium">Requested range<Input value={range} onChange={(event) => setRange(event.target.value)} placeholder="8000-8999 (optional)" /></label>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setOpen(false)} disabled={mutation.isPending}>Cancel</Button><Button onClick={submit} disabled={mutation.isPending || !project.trim() || !service.trim() || !hostId}>{mutation.isPending ? "Creating&" : "Create allocation"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
