import { NextResponse } from "next/server";

/**
 * Server-side upgrade cancellation proxy.
 *
 * POST /api/upgrades/{upgradeId}/cancel requires admin authorization.
 * Browser never holds PORTFORGE_ADMIN_BOOTSTRAP_TOKEN; this route
 * attaches it when forwarding to Central.
 */
export async function POST(
  _request: Request,
  context: { params: Promise<{ upgradeId: string }> },
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

  const { upgradeId } = await context.params;
  if (!upgradeId || !/^[0-9a-fA-F-]{36}$/.test(upgradeId)) {
    return NextResponse.json({ detail: "Invalid upgrade id." }, { status: 400 });
  }

  const centralBase =
    process.env.PORTFORGE_INTERNAL_API_URL?.trim() ||
    process.env.NEXT_PUBLIC_PORTFORGE_API_URL?.trim() ||
    "http://localhost:58000";

  const url = new URL(
    `/api/upgrades/${upgradeId}/cancel`,
    centralBase.endsWith("/") ? centralBase : `${centralBase}/`,
  );

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${bootstrap}`,
      },
      cache: "no-store",
    });
  } catch (cause) {
    return NextResponse.json(
      {
        detail: `Could not reach Central at ${centralBase} to cancel the upgrade.`,
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
      : `Central returned ${response.status} while cancelling the upgrade.`;

  return NextResponse.json({ detail }, { status: response.status });
}
