import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BindProbeBadge, getBindProbeMeta } from "./bind-probe-badge";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { BindProbeEvidence } from "@/lib/types/api";

function renderBadge(value: BindProbeEvidence) {
  return render(
    <TooltipProvider>
      <BindProbeBadge value={value} />
    </TooltipProvider>,
  );
}

describe("BindProbeBadge", () => {
  it.each<[BindProbeEvidence, string]>([
    ["verified_free", "Verified free"],
    ["verified_occupied", "Verified occupied"],
    ["expired", "Evidence expired"],
    ["unavailable", "Probe unavailable"],
    ["not_remote_capable", "Not remote-verified"],
  ])("renders the correct label for %s", (value, expectedLabel) => {
    renderBadge(value);
    expect(screen.getByText(expectedLabel)).toBeInTheDocument();
  });

  it("falls back to not_remote_capable for an unrecognized value rather than crashing", () => {
    renderBadge("some_future_value" as BindProbeEvidence);
    expect(screen.getByText("Not remote-verified")).toBeInTheDocument();
  });
});

// v1.1-D task Sec32 (MANDATORY): every UI representation of verified_free
// must never imply a permanent guarantee. This is the load-bearing test
// for that requirement -- verified against the actual strings shipped in
// the UI, not just described in a comment.
describe("verified_free wording never implies a permanent guarantee (task Sec32)", () => {
  const description = getBindProbeMeta("verified_free").description;

  it("states the approved, narrow meaning: verified free on the target host AT PROBE TIME", () => {
    expect(description).toContain("free on the target host at probe time");
  });

  it("explicitly disclaims a current/permanent guarantee", () => {
    expect(description.toLowerCase()).toContain("not a guarantee");
    expect(description.toLowerCase()).toContain("can still bind it afterward");
  });

  it("never claims the port is reserved or currently guaranteed by the probe itself", () => {
    const lower = description.toLowerCase();
    expect(lower).not.toContain("reserved by probe");
    expect(lower).not.toContain("currently guaranteed");
    expect(lower).not.toMatch(/\bguaranteed free\b/);
  });
});

describe("every bind_probe description is a full, standalone sentence (task Sec19)", () => {
  const values: BindProbeEvidence[] = [
    "verified_free",
    "verified_occupied",
    "expired",
    "unavailable",
    "not_remote_capable",
  ];

  it.each(values)("%s has a non-empty description usable outside the tooltip", (value) => {
    const meta = getBindProbeMeta(value);
    expect(meta.description.length).toBeGreaterThan(20);
    expect(meta.description.trim().endsWith(".")).toBe(true);
  });
});
