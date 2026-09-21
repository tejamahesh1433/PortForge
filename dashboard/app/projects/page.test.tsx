import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ProjectsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/projects",
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/hooks/use-projects", () => ({
  useProjects: () => ({
    isPending: false,
    isError: false,
    data: [{
      project_name: "shared-app", host_count: 2, port_count: 3, process_count: 1,
      docker_binding_count: 2, container_count: 1, reservation_count: 1, conflict_count: 0,
      healthy_host_count: 1, stale_host_count: 0, offline_host_count: 1,
      last_activity: null, hosts: ["host-a", "host-b"], entries: [],
    }],
  }),
}));

describe("ProjectsPage", () => {
  it("renders operational project summaries linked to detail", () => {
    render(<ProjectsPage />);
    const link = screen.getByRole("link", { name: /shared-app/i });
    expect(link).toHaveAttribute("href", "/projects/shared-app");
    expect(screen.getByText("2 hosts")).toBeInTheDocument();
    expect(screen.getByText("1 reservations")).toBeInTheDocument();
    expect(screen.getByText(/1 offline/)).toBeInTheDocument();
  });
});