export type MintEnrollmentTokenInput = {
  label?: string;
  ttl_hours?: number;
};

export type MintEnrollmentTokenResult = {
  enrollment_token: string;
  expires_at: string | null;
};

/**
 * Mint via the dashboard's server route (not Central directly), so the
 * admin bootstrap token never enters the browser bundle.
 */
export async function mintEnrollmentToken(
  input: MintEnrollmentTokenInput = {},
  signal?: AbortSignal,
): Promise<MintEnrollmentTokenResult> {
  let response: Response;
  try {
    response = await fetch("/api/enrollment-tokens", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        label: input.label?.trim() || undefined,
        ttl_hours: input.ttl_hours,
      }),
      signal,
    });
  } catch (cause) {
    throw new Error(
      `Could not reach the dashboard enrollment API.${cause instanceof Error ? ` ${cause.message}` : ""}`,
    );
  }

  const body = (await response.json().catch(() => null)) as
    | MintEnrollmentTokenResult
    | { detail?: string }
    | null;

  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : `Enrollment token mint failed (${response.status}).`;
    throw new Error(detail);
  }

  if (
    !body ||
    typeof body !== "object" ||
    !("enrollment_token" in body) ||
    typeof body.enrollment_token !== "string"
  ) {
    throw new Error("Enrollment token mint returned an unexpected response.");
  }

  return {
    enrollment_token: body.enrollment_token,
    expires_at: body.expires_at ?? null,
  };
}
