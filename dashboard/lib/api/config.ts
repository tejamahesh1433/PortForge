/**
 * Central's base URL, configurable via environment variable so it is
 * never hardcoded into components -- see README for local dev setup.
 * Falls back to the documented local Central dev URL only when the env
 * var is genuinely unset (e.g. a fresh checkout before .env.local is
 * created), never silently pointing anywhere unexpected in a deployed
 * build.
 */
export const PORTFORGE_API_URL: string =
  process.env.NEXT_PUBLIC_PORTFORGE_API_URL ?? "http://127.0.0.1:58000";
