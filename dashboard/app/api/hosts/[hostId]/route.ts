import { NextResponse } from "next/server";

/**
 * Server-side Remove Record proxy.
 *
 * Browser never holds PORTFORGE_ADMIN_BOOTSTRAP_TOKEN. This route attaches
 * it when calling Central DELETE /api/hosts/{id}.
 */
export async function DELETE(
  _request: Request,
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
    `/api/hosts/${hostId}`,
    centralBase.endsWith("/") ? centralBase : `${centralBase}/`,
  );

  let response: Response;
  try {
    response = await fetch(url, {
      method: "DELETE",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${bootstrap}`,
      },
      cache: "no-store",
    });
  } catch (cause) {
    return NextResponse.json(
      {
        detail: `Could not reach Central at ${centralBase} to remove the host record.`,
        cause: cause instanceof Error ? cause.message : String(cause),
      },
      { status: 502 },
    );
  }

  if (response.status === 204) {
    return new NextResponse(null, { status: 204 });
  }

  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { detail: text || response.statusText };
  }

  const detail =
    payload &&
    typeof payload === "object" &&
    "detail" in payload &&
    typeof (payload as { detail: unknown }).detail === "string"
      ? (payload as { detail: string }).detail
      : `Central returned ${response.status} while removing the host record.`;

  return NextResponse.json({ detail }, { status: response.status });
}
