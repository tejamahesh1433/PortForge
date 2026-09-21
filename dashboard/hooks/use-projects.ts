"use client";

import { useQuery } from "@tanstack/react-query";
import { getProject, listProjects } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import { queryKeys } from "./query-keys";

/** GET /api/projects */
export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: ({ signal }) => listProjects(signal),
    staleTime: STALE_TIME_MS,
  });
}

export function useProject(projectName: string | undefined) {
  return useQuery({
    queryKey: queryKeys.projects.detail(projectName ?? ""),
    queryFn: ({ signal }) => getProject(projectName as string, signal),
    enabled: Boolean(projectName),
    staleTime: STALE_TIME_MS,
  });
}