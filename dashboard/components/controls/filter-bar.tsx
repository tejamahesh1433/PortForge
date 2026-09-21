import type { ReactNode } from "react";

/** Layout container for a page's filter controls (search + any number of
 * FilterSelect dropdowns) -- wraps responsively rather than overflowing
 * on narrower screens.
 */
export function FilterBar({ children }: { children: ReactNode }) {
  return <div className="mb-4 flex flex-wrap items-center gap-2">{children}</div>;
}
