"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useHosts } from "@/hooks/use-hosts";
import { usePorts } from "@/hooks/use-ports";
import { useProjects } from "@/hooks/use-projects";
import { HardDrive, Network, FolderGit2 } from "lucide-react";

export function GlobalSearch() {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  const { data: hostsData } = useHosts();
  const { data: portsData } = usePorts();
  const { data: projectsData } = useProjects();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((open) => !open);
      }
    };

    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const runCommand = (command: () => void) => {
    setOpen(false);
    command();
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="Open global search" className="hidden text-sm text-muted-foreground bg-muted/50 border rounded-md px-3 py-1.5 sm:flex items-center justify-between w-44 lg:w-64 hover:bg-muted transition-colors"
      >
        <span>Search...</span>
        <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground opacity-100">
          <span className="text-xs">⌘</span>K
        </kbd>
      </button>
      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput placeholder="Type a command or search..." />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          
          {projectsData && projectsData.length > 0 && (
            <CommandGroup heading="Projects">
              {projectsData.map((project) => (
                <CommandItem
                  key={project.project_name}
                  value={`project ${project.project_name}`}
                  onSelect={() => runCommand(() => router.push(`/projects/${encodeURIComponent(project.project_name)}`))}
                >
                  <FolderGit2 className="mr-2 h-4 w-4" />
                  <span className="flex-1">{project.project_name}</span><span className="text-xs text-muted-foreground">Project</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}

          {hostsData?.items && hostsData.items.length > 0 && (
            <CommandGroup heading="Hosts">
              {hostsData.items.map((host) => (
                <CommandItem
                  key={host.id}
                  value={`host ${host.hostname} ${host.display_name || ""}`}
                  onSelect={() => runCommand(() => router.push(`/hosts/${host.id}`))}
                >
                  <HardDrive className="mr-2 h-4 w-4" />
                  <span className="flex-1">{host.display_name || host.hostname}</span><span className="text-xs text-muted-foreground">Host</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}

          {portsData?.items && portsData.items.length > 0 && (
            <CommandGroup heading="Ports">
              {portsData.items.map((portObs) => (
                <CommandItem
                  key={portObs.id}
                  value={`port ${portObs.port} ${portObs.protocol} ${portObs.service_name || ""} ${portObs.process_name || ""}`}
                  onSelect={() => runCommand(() => router.push(`/ports?port=${portObs.port}`))}
                >
                  <Network className="mr-2 h-4 w-4" />
                  <span>{portObs.port}/{portObs.protocol}</span><span className="ml-2 text-xs text-muted-foreground">· {portObs.host_hostname ?? "Unknown host"}</span>
                  {portObs.service_name && <span className="ml-2 text-muted-foreground">- {portObs.service_name}</span>}
                  {portObs.process_name && <span className="ml-2 text-muted-foreground">({portObs.process_name})</span>}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
        </CommandList>
      </CommandDialog>
    </>
  );
}

