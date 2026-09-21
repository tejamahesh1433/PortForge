import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});
import { CommandDialog, CommandInput, CommandList } from "./command";

describe("CommandDialog", () => {
  it("provides the command store to compound children", () => {
    render(
      <CommandDialog open onOpenChange={() => undefined}>
        <CommandInput placeholder="Search commands" />
        <CommandList />
      </CommandDialog>,
    );

    expect(screen.getByPlaceholderText("Search commands")).toBeInTheDocument();
  });
});