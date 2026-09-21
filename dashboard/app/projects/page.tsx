"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { Activity, AlertTriangle, Box, Container, Folder, Server } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { LoadingState } from "@/components/feedback/loading-state";
import { PageHeader } from "@/components/layout/page-header";
import { SearchInput } from "@/components/controls/search-input";
import { FilterSelect } from "@/components/controls/filter-select";
import { ActiveFilters } from "@/components/controls/active-filters";
import { useProjects } from "@/hooks/use-projects";
import type { ProjectOut } from "@/lib/types/api";

function ProjectCard({ project }: { project: ProjectOut }) {
  const healthLabel = project.offline_host_count
    ? `${project.offline_host_count} offline`
    : project.stale_host_count
      ? `${project.stale_host_count} stale`
      : `${project.healthy_host_count} healthy`;

  return (
    <Link href={`/projects/${encodeURIComponent(project.project_name)}`} className="block rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <Card className="h-full border-border bg-card transition-colors hover:border-primary/40">
        <CardHeader className="gap-2 border-b border-border/60 pb-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">{project.project_name}</p>
              <p className="mt-1 truncate text-xs text-muted-foreground">{project.hosts.join(" · ") || "Reservation only"}</p>
            </div>
            <Folder className="size-4 shrink-0 text-primary" aria-hidden="true" />
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-x-4 gap-y-3 pt-1 text-xs">
          <span className="flex items-center gap-1.5 text-muted-foreground"><Server className="size-3.5" /> {project.host_count} hosts</span>
          <span className="flex items-center gap-1.5 text-muted-foreground"><Activity className="size-3.5" /> {project.port_count} bindings</span>
          <span className="flex items-center gap-1.5 text-muted-foreground"><Box className="size-3.5" /> {project.process_count} process</span>
          <span className="flex items-center gap-1.5 text-muted-foreground"><Container className="size-3.5" /> {project.docker_binding_count} Docker</span>
          <span className="text-muted-foreground">{project.reservation_count} reservations</span>
          <span className={project.conflict_count ? "flex items-center gap-1 text-red-400" : "text-muted-foreground"}>
            {project.conflict_count ? <AlertTriangle className="size-3.5" /> : null}{project.conflict_count} conflicts
          </span>
          <span className="col-span-2 border-t border-border/60 pt-2 text-muted-foreground">Freshness: {healthLabel}</span>
        </CardContent>
      </Card>
    </Link>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense fallback={<LoadingState variant="cards" rows={6} />}>
      <ProjectsPageContent />
    </Suspense>
  );
}

function ProjectsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const projects = useProjects();
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [health, setHealth] = useState(searchParams.get("health") ?? "all");

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (search) params.set("q", search); else params.delete("q");
    if (health !== "all") params.set("health", health); else params.delete("health");
    router.replace(`${pathname}?${params.toString()}`);
  }, [search, health, pathname, router, searchParams]);

  const filtered = useMemo(() => (projects.data ?? []).filter((project) => {
    if (!project.project_name.toLowerCase().includes(search.trim().toLowerCase())) return false;
    if (health === "offline" && project.offline_host_count === 0) return false;
    if (health === "stale" && project.stale_host_count === 0) return false;
    if (health === "healthy" && (project.host_count === 0 || project.healthy_host_count !== project.host_count)) return false;
    return true;
  }), [projects.data, search, health]);

  const resetFilters = () => {
    setSearch("");
    setHealth("all");
  };

  return (
    <div>
      <PageHeader title="Projects" description="Operational project contexts aggregated across physical hosts." />
      <div className="mb-3 flex flex-wrap gap-3">
        <SearchInput value={search} onChange={setSearch} placeholder="Search projects…" className="w-64" />
        <FilterSelect label="Freshness" value={health} onChange={setHealth} options={[
          { value: "healthy", label: "All healthy" },
          { value: "stale", label: "Contains stale" },
          { value: "offline", label: "Contains offline" },
        ]} />
      </div>
      <ActiveFilters
        filters={[
          ...(search ? [{ label: "Search", value: search, onRemove: () => setSearch("") }] : []),
          ...(health !== "all" ? [{ label: "Freshness", value: health, onRemove: () => setHealth("all") }] : []),
        ]}
        onReset={resetFilters}
      />
      {projects.isPending ? <LoadingState variant="cards" rows={6} /> : projects.isError ? (
        <ErrorState error={projects.error} onRetry={() => void projects.refetch()} />
      ) : filtered.length === 0 ? (
        <EmptyState icon={Folder} title={search ? "No matching projects" : "No projects detected yet"} description="Projects appear from detected bindings or reservations reported to Central." />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((project) => <ProjectCard key={project.project_name} project={project} />)}
        </div>
      )}
    </div>
  );
}
