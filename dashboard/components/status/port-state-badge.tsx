import { StatusBadge } from "./status-badge";

/** Thin, explicit alias over StatusBadge for a port's lifecycle state
 * (ACTIVE/FREE/RESERVED/CONFLICT/SYSTEM -- PortObservationOut.state).
 * Kept as its own component (per the required component list) so call
 * sites read as domain concepts, not a generic badge.
 */
export function PortStateBadge({ state, compact }: { state: string; compact?: boolean }) {
  return <StatusBadge value={state} compact={compact} />;
}
