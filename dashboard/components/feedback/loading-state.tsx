import { Skeleton } from "@/components/ui/skeleton";

interface LoadingStateProps {
  /** Visual shape to skeleton-render, matching the content it stands in for. */
  variant?: "table" | "cards" | "block";
  rows?: number;
}

/** Skeleton loading placeholder, shaped to match what's actually loading
 * (a table, a card grid, or a generic block) so the transition to real
 * content doesn't jump the layout around.
 */
export function LoadingState({ variant = "block", rows = 6 }: LoadingStateProps) {
  if (variant === "table") {
    return (
      <div className="space-y-2" role="status" aria-label="Loading">
        {Array.from({ length: rows }).map((_, index) => (
          <Skeleton key={index} className="h-10 w-full rounded-md" />
        ))}
      </div>
    );
  }

  if (variant === "cards") {
    return (
      <div
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
        role="status"
        aria-label="Loading"
      >
        {Array.from({ length: rows }).map((_, index) => (
          <Skeleton key={index} className="h-36 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3" role="status" aria-label="Loading">
      <Skeleton className="h-6 w-1/3 rounded-md" />
      <Skeleton className="h-32 w-full rounded-lg" />
    </div>
  );
}
