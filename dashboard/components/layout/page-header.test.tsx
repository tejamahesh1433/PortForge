import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageHeader } from "./page-header";

describe("PageHeader breadcrumbs", () => {
  it("renders no breadcrumb nav when omitted", () => {
    render(<PageHeader title="Ports" />);
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).not.toBeInTheDocument();
  });

  it("renders a linked ancestor and a non-link current page, e.g. Hosts > NTMKEYA", () => {
    render(<PageHeader title="NTMKEYA" breadcrumbs={[{ label: "Hosts", href: "/hosts" }, { label: "NTMKEYA" }]} />);

    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    const link = screen.getByRole("link", { name: "Hosts" });
    expect(link).toHaveAttribute("href", "/hosts");

    const current = screen.getByText("NTMKEYA", { selector: "[aria-current='page']" });
    expect(nav).toContainElement(current);
  });
});
