import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PortInspector } from "./port-inspector";
import type { PortObservationOut } from "@/lib/types/api";

vi.mock("@/hooks/use-hosts", () => ({
  useHost: () => ({ isPending: true, isError: false, data: undefined }),
}));

vi.mock("@/components/activity/activity-feed", () => ({
  ActivityFeed: () => <div data-testid="activity-feed-stub" />,
}));

const basePort: PortObservationOut = {
  id: "port-1",
  host_id: "host-1",
  host_hostname: "NTMKEYA",
  port: 8080,
  protocol: "tcp",
  bind_address: "0.0.0.0",
  state: "active",
  source: "process",
  pid: 4242,
  process_name: "node.exe",
  process_path: "C:\\app\\node.exe",
  working_directory: null,
  container_id: null,
  container_name: null,
  container_image: null,
  container_port: null,
  docker_compose_project: null,
  service_name: null,
  project_name: null,
  purpose: null,
  category: null,
  detection_confidence: null,
  first_seen: "2026-09-18T00:00:00Z",
  last_seen: "2026-09-18T00:05:00Z",
  observed_at: "2026-09-18T00:05:00Z",
};

describe("PortInspector", () => {
  it("omits the project-context section when no project/service/purpose is reported", () => {
    render(<PortInspector port={basePort} open onOpenChange={vi.fn()} />);
    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.queryByText("Project context")).not.toBeInTheDocument();
  });

  it("shows Docker mapping instead of Owner, and project context when present", () => {
    const dockerPort: PortObservationOut = {
      ...basePort,
      source: "docker",
      pid: null,
      process_name: null,
      process_path: null,
      container_name: "ocrforge-api",
      container_id: "abc123",
      project_name: "ocrforge",
    };
    render(<PortInspector port={dockerPort} open onOpenChange={vi.fn()} />);
    expect(screen.getByText("Docker mapping")).toBeInTheDocument();
    expect(screen.queryByText("Owner")).not.toBeInTheDocument();
    expect(screen.getByText("Project context")).toBeInTheDocument();
  });

  it("copies a value to the clipboard when its copy button is clicked", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<PortInspector port={basePort} open onOpenChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy PID" }));

    expect(writeText).toHaveBeenCalledWith("4242");
  });
});
