import { ListActivityParams, ActivityResponse } from "../types/api";
import { portforgeFetch } from "./client";

export async function listActivity(
  params?: ListActivityParams,
  signal?: AbortSignal,
): Promise<ActivityResponse> {
  return portforgeFetch<ActivityResponse>("/api/activity", {
    params,
    signal,
  });
}
