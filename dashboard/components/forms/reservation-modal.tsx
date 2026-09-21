"use client";

import { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useHosts } from "@/hooks/use-hosts";
import { useCreateDashboardReservation } from "@/hooks/use-reservations";
import { useHostPorts } from "@/hooks/use-hosts";
import { AlertCircle, CheckCircle2, Lock, Loader2 } from "lucide-react";
import { toast } from "@/components/ui/toast";

interface ReservationModalProps {
  defaultHostId?: string;
  defaultPort?: string;
  defaultProtocol?: "tcp" | "udp";
  defaultService?: string;
  defaultProject?: string;
  trigger?: React.ReactElement;
}

export function ReservationModal({ 
  defaultHostId = "", 
  defaultPort = "", 
  defaultProtocol = "tcp",
  defaultService = "",
  defaultProject = "",
  trigger 
}: ReservationModalProps = {}) {
  const [open, setOpen] = useState(false);
  const [hostId, setHostId] = useState(defaultHostId);
  const [port, setPort] = useState(defaultPort);
  const [protocol, setProtocol] = useState<"tcp" | "udp">(defaultProtocol);
  const [project, setProject] = useState(defaultProject);
  const [service, setService] = useState(defaultService);
  const [purpose, setPurpose] = useState("");

  const { data: hostsData } = useHosts();
  const portsQuery = useHostPorts(hostId || undefined);
  const createMutation = useCreateDashboardReservation();

  const isPortTaken = portsQuery.data?.some(p => p.port === parseInt(port) && p.protocol === protocol);

  const resetForm = () => {
    setHostId(defaultHostId);
    setPort(defaultPort);
    setProtocol(defaultProtocol);
    setProject(defaultProject);
    setService(defaultService);
    setPurpose("");
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!hostId || !port || !project) return;

    createMutation.mutate(
      {
        host_id: hostId,
        port: parseInt(port),
        protocol,
        project,
        service: service || undefined,
        purpose: purpose || undefined,
      },
      {
        onSuccess: () => {
          toast.add({
            type: "success",
            title: "Reservation created",
            description: `Port ${port}/${protocol} reserved for ${project}.`,
          });
          setOpen(false);
          resetForm();
        },
        onError: (error) => {
          toast.add({
            type: "error",
            title: "Failed to create reservation",
            description: error instanceof Error ? error.message : "Unknown error occurred.",
          });
        },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={(val) => { setOpen(val); if (!val) resetForm(); }}>
      <DialogTrigger render={trigger || (
        <Button size="sm" className="gap-1.5">
          <Lock className="size-3.5" />
          Reserve Port
        </Button>
      )} />
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Create Port Reservation</DialogTitle>
          <DialogDescription>
            Reserve a port on a specific host to prevent conflicts. 
            This reservation will sync down to the agent automatically.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} className="space-y-4 pt-4">
          <div className="space-y-2">
            <Label htmlFor="host">Target Host</Label>
            <Select value={hostId} onValueChange={(val) => setHostId(val || "")} required>
              <SelectTrigger>
                <SelectValue placeholder="Select a host...">
                  {(value: string | null) => {
                    if (!value) return "Select a host...";
                    const host = hostsData?.items?.find((h) => h.id === value);
                    return host?.display_name ?? host?.hostname ?? value;
                  }}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {hostsData?.items?.map((host) => (
                  <SelectItem key={host.id} value={host.id}>
                    {host.display_name || host.hostname}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="port">Port</Label>
              <Input
                id="port"
                type="number"
                min="1"
                max="65535"
                placeholder="8080"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="protocol">Protocol</Label>
              <Select value={protocol} onValueChange={(val) => setProtocol(val as "tcp" | "udp")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="tcp">TCP</SelectItem>
                  <SelectItem value="udp">UDP</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {hostId && port && (
            <div className={`text-xs px-3 py-2 rounded-md flex items-center gap-2 ${isPortTaken ? 'bg-destructive/10 text-destructive' : 'bg-green-500/10 text-green-600 dark:text-green-400'}`}>
              {isPortTaken ? (
                <><AlertCircle className="size-4" /> Port is currently ACTIVE on this host. Reserving it may cause a conflict.</>
              ) : (
                <><CheckCircle2 className="size-4" /> Port appears free based on latest observations.</>
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="project">Project Name</Label>
            <Input
              id="project"
              placeholder="e.g. data-pipeline"
              value={project}
              onChange={(e) => setProject(e.target.value)}
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="service">Service (Optional)</Label>
              <Input
                id="service"
                placeholder="e.g. api"
                value={service}
                onChange={(e) => setService(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="purpose">Purpose (Optional)</Label>
              <Input
                id="purpose"
                placeholder="e.g. database"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              />
            </div>
          </div>

          <div className="flex justify-end pt-4">
            <Button type="submit" disabled={!hostId || !port || !project || createMutation.isPending}>
              {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Reserve Port
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

