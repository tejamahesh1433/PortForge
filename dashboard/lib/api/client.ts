import { PORTFORGE_API_URL } from "./config";

/**
 * Thrown for any non-2xx response from Central. Carries the HTTP status
 * and, where Central returned one, its own error `detail` string (FastAPI's
 * standard error body shape: `{"detail": "..."}`) so callers/UI can show a
 * real, specific message instead of a generic "something went wrong".
 */
export class PortForgeApiError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(status: number, detail: string | null, message: string) {
    super(message);
    this.name = "PortForgeApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Thrown when Central could not be reached at all (network failure, DNS,
 * connection refused, timeout) -- distinct from PortForgeApiError, which
 * means Central *did* respond, just with an error status.
 */
export class PortForgeConnectionError extends Error {
  constructor(cause: unknown) {
    super(
      `Could not reach Central at ${PORTFORGE_API_URL}. Is it running?`,
      { cause },
    );
    this.name = "PortForgeConnectionError";
  }
}

export type QueryParamValue = string | number | boolean | undefined | null;

function buildUrl(path: string, params?: object): string {
  const url = new URL(path.replace(/^\//, ""), `${PORTFORGE_API_URL}/`);
  if (params) {
    // Resource-specific param interfaces (ListHostsParams etc., see
    // lib/types/api.ts) intentionally declare only their known optional
    // keys rather than a generic index signature, so this cast is the
    // one place that bridges "a plain object of query params" to
    // "key/value pairs safe to stringify" -- every value's actual type
    // is still QueryParamValue by construction at every call site.
    for (const [key, value] of Object.entries(params as Record<string, QueryParamValue>)) {
      if (value !== undefined && value !== null && value !== "" && !Number.isNaN(value)) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function parseErrorDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.clone().json()) as unknown;
    if (
      body &&
      typeof body === "object" &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
    ) {
      return (body as { detail: string }).detail;
    }
  } catch {
    // Response body wasn't JSON (or was JSON without a `detail` string) --
    // fall through and report status text only.
  }
  return null;
}

/**
 * The single low-level entry point every resource-specific API function
 * goes through. Never called directly from components -- see lib/api/*.ts
 * for the typed, resource-specific functions React code should use.
 */
export async function portforgeFetch<T>(
  path: string,
  options: {
    params?: object;
    body?: unknown;
    method?: string;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const url = buildUrl(path, options.params);

  let response: Response;
  const headers: HeadersInit & Record<string, string> = { Accept: "application/json" };
  if (options.body) {
    headers["Content-Type"] = "application/json";
  }

  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (cause) {
    throw new PortForgeConnectionError(cause);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    throw new PortForgeApiError(
      response.status,
      detail,
      detail ?? `Central returned ${response.status} ${response.statusText} for ${path}`,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
