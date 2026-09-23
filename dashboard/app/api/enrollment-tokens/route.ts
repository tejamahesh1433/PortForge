import { NextResponse } from "next/server";

/**
 * Server-side enrollment-token mint.
 *
 * The admin bootstrap token stays in dashboard process env
 * (`PORTFORGE_ADMIN_BOOTSTRAP_TOKEN`) and is never sent to the browser.
 * The browser only talks to this route; this route talks to Central.
 */
export async function POST(request: Request) {
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

  const centralBase =
    process.env.PORTFORGE_INTERNAL_API_URL?.trim() ||
    process.env.NEXT_PUBLIC_PORTFORGE_API_URL?.trim() ||
    "http://localhost:58000";

  let label: string | undefined;
  let ttlHours = 24;
  try {
    const body = (await request.json()) as { label?: unknown; ttl_hours?: unknown };
    if (typeof body.label === "string" && body.label.trim()) {
      label = body.label.trim().slice(0, 120);
    }
    if (typeof body.ttl_hours === "number" && Number.isFinite(body.ttl_hours)) {
      ttlHours = Math.max(1, Math.min(168, Math.floor(body.ttl_hours)));
    } else if (typeof body.ttl_hours === "string" && body.ttl_hours.trim()) {
      const parsed = Number(body.ttl_hours);
      if (Number.isFinite(parsed)) {
        ttlHours = Math.max(1, Math.min(168, Math.floor(parsed)));
      }
    }
  } catch {
    // empty / non-JSON body is fine -- defaults apply
  }

  const url = new URL("/api/agent/enrollment-tokens", centralBase.endsWith("/") ? centralBase : `${centralBase}/`);
  if (label) url.searchParams.set("label", label);
  url.searchParams.set("ttl_hours", String(ttlHours));

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
        detail: `Could not reach Central at ${centralBase} to mint an enrollment token.`,
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

  if (!response.ok) {
    const detail =
      payload &&
      typeof payload === "object" &&
      "detail" in payload &&
      typeof (payload as { detail: unknown }).detail === "string"
        ? (payload as { detail: string }).detail
        : `Central returned ${response.status} while minting an enrollment token.`;
    return NextResponse.json({ detail }, { status: response.status });
  }

  return NextResponse.json(payload);
}
