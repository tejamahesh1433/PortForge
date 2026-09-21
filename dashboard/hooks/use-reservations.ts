"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listReservations, createDashboardReservation, deleteDashboardReservation } from "@/lib/api/resources";
import { STALE_TIME_MS } from "@/lib/query-config";
import type { ListReservationsParams } from "@/lib/types/api";
import { queryKeys } from "./query-keys";

/** GET /api/reservations */
export function useReservations(params: ListReservationsParams = {}) {
  return useQuery({
    queryKey: queryKeys.reservations.list(params),
    queryFn: ({ signal }) => listReservations(params, signal),
    staleTime: STALE_TIME_MS,
  });
}

/** POST /api/reservations/dashboard */
export function useCreateDashboardReservation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: import("@/lib/types/api").DashboardReservationIn) =>
      createDashboardReservation(payload),
    onSuccess: () => {
      // Invalidate relevant caches to trigger a refetch
      queryClient.invalidateQueries({ queryKey: ["reservations"] });
      queryClient.invalidateQueries({ queryKey: ["ports"] });
      queryClient.invalidateQueries({ queryKey: ["conflicts"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["activity"] });
    },
  });
}

/** DELETE /api/reservations/dashboard/{host_id}/{reservation_id} */
export function useDeleteDashboardReservation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ hostId, reservationId }: { hostId: string; reservationId: string }) =>
      deleteDashboardReservation(hostId, reservationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reservations"] });
      queryClient.invalidateQueries({ queryKey: ["ports"] });
      queryClient.invalidateQueries({ queryKey: ["conflicts"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["activity"] });
    },
  });
}
