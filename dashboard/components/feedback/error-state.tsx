import { RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PortForgeApiError, PortForgeConnectionError } from "@/lib/api/client";

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
}

/** Every failed query in this app renders through this component --
 * errors are never silently swallowed. Distinguishes "Central responded
 * with an error" (PortForgeApiError, shows its real detail) from
 * "Central couldn't be reached at all" (PortForgeConnectionError) so the
 * message tells the operator what actually happened.
 */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const message = describeError(error);

  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-red-500/20 bg-red-500/5 px-6 py-16 text-center">
      <span className="flex size-10 items-center justify-center rounded-full bg-red-500/10 text-red-400">
        <TriangleAlert className="size-5" aria-hidden="true" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">Something went wrong</p>
        <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Try again
        </Button>
      )}
    </div>
  );
}

function describeError(error: unknown): string {
  if (error instanceof PortForgeConnectionError) {
    return error.message;
  }
  if (error instanceof PortForgeApiError) {
    return `Central error (${error.status}): ${error.detail ?? error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "An unexpected error occurred.";
}
