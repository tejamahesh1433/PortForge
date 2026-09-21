import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ProjectDetailPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: () => ({ projectId: "shared-app" }),
  useSearchParams: () => new URLSearchParams("tab=overview"),
  useRouter: () => ({ replace }),
}));
vi.mock("@/hooks/use-projects", () => ({
  useProject: () => ({ isPending: false, isError: false, refetch: vi.fn(), data: {
    project_name: "shared-app", host_count: 2, port_count: 2, process_count: 1,
    docker_binding_count: 1, container_count: 1, reservation_count: 0, conflict_count: 0,
    healthy_host_count: 1, stale_host_count: 0, offline_host_count: 1, last_activity: null,
    hosts: ["host-a", "host-b"], entries: [],
    host_details: [
      { host_id: "a", hostname: "host-a", operating_system: "linux", docker_available: true, health_state: "HEALTHY", health_reason: "AGENT_HEALTHY", age_seconds: 5, snapshot_age_seconds: 5, binding_count: 1 },
      { host_id: "b", hostname: "host-b", operating_system: "windows", docker_available: false, health_state: "OFFLINE", health_reason: "AGENT_OFFLINE", age_seconds: 600, snapshot_age_seconds: 600, binding_count: 1 },
    ],
    ports: { total: 2, limit: 200, offset: 0, items: [] },
    reservations: { total: 0, limit: 100, offset: 0, items: [] }, conflicts: [], activity: [],
  } }),
}));
vi.mock("@/hooks/use-reservations", () => ({ useDeleteDashboardReservation: () => ({ mutate: vi.fn() }) }));
vi.mock("@/hooks/use-recommendation", () => ({ useRecommendation: () => ({ isPending: false, isError: false }) }));
vi.mock("@/components/data/port-table", () => ({ PortTable: () => <div>Shared Port Inspector table</div> }));

describe("ProjectDetailPage", () => {
  it("renders multi-host topology and stale-data warning", () => {
    render(<ProjectDetailPage />);
    expect(screen.getByText("Multi-host project context across 2 physical hosts.")).toBeInTheDocument();
    expect(screen.getByText(/last-known host state/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "host-a" })).toHaveAttribute("href", "/hosts/a");
    expect(screen.getByText("No project conflicts")).toBeInTheDocument();
  });
});