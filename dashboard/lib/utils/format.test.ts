import { describe, expect, it } from "vitest";
import { formatAbsoluteTime, formatRelativeTime, titleCase } from "./format";

describe("formatRelativeTime", () => {
  const now = new Date("2026-01-01T12:00:00Z");

  it("shows 'just now' for very recent timestamps", () => {
    expect(formatRelativeTime(new Date(now.getTime() - 2000).toISOString(), now)).toBe("just now");
  });

  it("shows seconds for under a minute", () => {
    expect(formatRelativeTime(new Date(now.getTime() - 30_000).toISOString(), now)).toBe("30s ago");
  });

  it("shows minutes for under an hour", () => {
    expect(formatRelativeTime(new Date(now.getTime() - 5 * 60_000).toISOString(), now)).toBe("5m ago");
  });

  it("shows hours for under a day", () => {
    expect(formatRelativeTime(new Date(now.getTime() - 3 * 3_600_000).toISOString(), now)).toBe("3h ago");
  });

  it("shows days for under a month", () => {
    expect(formatRelativeTime(new Date(now.getTime() - 4 * 86_400_000).toISOString(), now)).toBe("4d ago");
  });

  it("falls back to a locale date for very old timestamps", () => {
    const old = new Date(now.getTime() - 90 * 86_400_000);
    expect(formatRelativeTime(old.toISOString(), now)).toBe(old.toLocaleDateString());
  });

  it("returns 'unknown' for an unparsable timestamp", () => {
    expect(formatRelativeTime("not-a-date", now)).toBe("unknown");
  });
});

describe("formatAbsoluteTime", () => {
  it("formats a valid ISO timestamp", () => {
    const iso = "2026-01-01T12:00:00Z";
    expect(formatAbsoluteTime(iso)).toBe(new Date(iso).toLocaleString());
  });

  it("returns 'unknown' for an unparsable timestamp", () => {
    expect(formatAbsoluteTime("garbage")).toBe("unknown");
  });
});

describe("titleCase", () => {
  it("title-cases a snake_case token", () => {
    expect(titleCase("docker_compose")).toBe("Docker Compose");
  });

  it("title-cases a plain lowercase word", () => {
    expect(titleCase("active")).toBe("Active");
  });

  it("handles multiple separators", () => {
    expect(titleCase("multi-word_example token")).toBe("Multi Word Example Token");
  });
});
