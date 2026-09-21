import { describe, expect, it } from "vitest";
import { getStatusMeta } from "./status-meta";

describe("getStatusMeta", () => {
  it("resolves a known lowercase value", () => {
    const meta = getStatusMeta("docker");
    expect(meta.label).toBe("Docker");
  });

  it("is case-insensitive against the real backend's uppercase PortState values", () => {
    const meta = getStatusMeta("ACTIVE");
    expect(meta.label).toBe("Active");
  });

  it("resolves every known port state without falling back", () => {
    for (const state of ["ACTIVE", "FREE", "RESERVED", "CONFLICT", "SYSTEM"]) {
      expect(getStatusMeta(state).label).not.toBe("Unknown");
    }
  });

  it("resolves every known observation source without falling back", () => {
    for (const source of ["process", "docker", "system"]) {
      expect(getStatusMeta(source).label).not.toBe("Unknown");
    }
  });

  it("degrades to a neutral fallback for an unrecognized value, preserving the raw string", () => {
    const meta = getStatusMeta("some_future_state");
    expect(meta.label).toBe("some_future_state");
  });

  it("never throws for an empty string", () => {
    expect(() => getStatusMeta("")).not.toThrow();
  });
});
