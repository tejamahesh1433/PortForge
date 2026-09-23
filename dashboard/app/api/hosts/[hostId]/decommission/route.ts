import { NextResponse } from "next/server";

/**
 * Server-side Decommission proxy.
 *
 * Browser never holds PORTFORGE_ADMIN_BOOTSTRAP_TOKEN. This route attaches
 * it when calling Central POST /api/hosts/{id}/decommission.
 */
export async function POST(
  request: Request,
  context: { params: Promise<{ hostId: string }> },
) {
  const bootstrap = process.env.PORTFORGE_ADMIN_BOOTSTRAP_TOKEN?.trim();
  if (!bootstrap) {
    return NextResponse.json(
      {
        detail:
          "Dashboard is missing PORTFORGE_ADMIN_BOOTSTRAP_TOKEN. Set the same admin secret used by Central, then restart the dashboard.",
      },
      { status: 503 },
    );
  }

  const { hostId } = await context.params;
  if (!hostId || !/^[0-9a-fA-F-]{36}$/.test(hostId)) {
    return NextResponse.json({ detail: "Invalid host id." }, { status: 400 });
  }

  const centralBase =
    process.env.PORTFORGE_INTERNAL_API_URL?.trim() ||
    process.env.NEXT_PUBLIC_PORTFORGE_API_URL?.trim() ||
    "http://localhost:58000";

  const url = new URL(
    `/api/hosts/${hostId}/decommission`,
    centralBase.endsWith("/") ? centralBase : `${centralBase}/`,
  );

  let body: string | undefined;
  try {
    const text = await request.text();
    body = text || undefined;
  } catch {
    body = undefined;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Authorization: `Bearer ${bootstrap}`,
      },
      body,
      cache: "no-store",
    });
  } catch (cause) {
    return NextResponse.json(
      {
        detail: `Could not reach Central at ${centralBase} to decommission the host.`,
        cause: cause instanceof Error ? cause.message : String(cause),
      },
      { status: 502 },
    );
  }

  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { detail: text || response.statusText };
  }

  if (response.ok) {
    return NextResponse.json(payload, { status: response.status });
  }

  const detail =
    payload &&
    typeof payload === "object" &&
    "detail" in payload &&
    typeof (payload as { detail: unknown }).detail === "string"
      ? (payload as { detail: string }).detail
      : `Central returned ${response.status} while decommissioning the host.`;

  return NextResponse.json({ detail }, { status: response.status });
}
