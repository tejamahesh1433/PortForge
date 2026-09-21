import { StatusBadge } from "./status-badge";

/** Thin, explicit alias over StatusBadge for a port observation's source
 * (process/docker/system -- PortObservationOut.source).
 */
export function PortSourceBadge({ source, compact }: { source: string; compact?: boolean }) {
  return <StatusBadge value={source} compact={compact} />;
}
