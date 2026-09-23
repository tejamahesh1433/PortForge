"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { mintEnrollmentToken, type MintEnrollmentTokenInput } from "@/lib/api/enrollment";

export function useMintEnrollmentToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: MintEnrollmentTokenInput) => mintEnrollmentToken(input),
    onSuccess: () => {
      // Host appears only after the agent enrolls; keep the list fresh.
      void queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}
