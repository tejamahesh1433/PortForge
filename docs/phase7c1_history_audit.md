# Phase 7C.1 History Audit

## Existing Models & Capabilities

1. **Current binding table/model**: `CurrentPortObservation`
   - **What it does:** Maintains exactly one row per physical identity (host, port, protocol, bind_address) representing the *current* state. Upserted on snapshot; deleted when it disappears.
   - **Timestamps:** `first_seen`, `last_seen`, `observed_at`.

2. **Snapshot/observation history model**: `PortObservationEvent`
   - **What it does:** An append-only log of *meaningful changes* to physical bindings.
   - **Event Types:** `appeared`, `changed`, `disappeared`.
   - **Timestamps:** `occurred_at`.
   - **Capabilities:** Can reliably answer "A port appeared" and "A port disappeared". Does not capture non-port events like reservations or host status.

3. **Host heartbeat/last-seen persistence**: `Host`
   - **What it does:** Tracks `first_seen`, `last_seen`, `status`, and `docker_available`.
   - **Capabilities:** Only stores the *current* state. Cannot reliably answer "A host went offline at T1 and returned at T2" or "Docker became unavailable" because history is overwritten on update. 

4. **Reservation lifecycle persistence**: `CentralReservation`
   - **What it does:** Stores active reservations.
   - **Capabilities:** Only stores *active* reservations (`created_at`). When a reservation is released, the row is deleted. Cannot answer "A reservation was released" because the record is gone.

5. **Conflict persistence or derivation**: Derived dynamically in `conflict_service.py`
   - **What it does:** Joins active reservations against current port observations.
   - **Capabilities:** Cannot derive historical conflicts. Once a conflict is resolved (e.g., process stops), the current observation is removed, and the conflict vanishes with zero historical record.

6. **Project history capabilities**:
   - **Capabilities:** Implicitly derived from current port observations and reservations. No separate project events.

7. **Existing timestamps and indexes**:
   - `PortObservationEvent` has `occurred_at`, `host_id`, `port`, `protocol` indexes.
   - `CurrentPortObservation` has `host_id` and unique constraints.

8. **Existing retention behavior**:
   - `CurrentPortObservation` and `CentralReservation` clean themselves up (deleted on resolution).
   - `PortObservationEvent` grows unbounded. There is no automated cleanup strategy for history.

## Gaps Requiring Schema Changes

While `PortObservationEvent` tracks port bindings durably, we cannot derive the following events from existing persisted state:
- `HOST_ONLINE` / `HOST_OFFLINE`
- `DOCKER_AVAILABLE` / `DOCKER_UNAVAILABLE`
- `RESERVATION_CREATED` / `RESERVATION_RELEASED`
- `CONFLICT_DETECTED` / `CONFLICT_RESOLVED`

Because status transitions are overwritten, reservations are DELETED, and conflicts are DYNAMICALLY evaluated.

## Conclusion

To implement a robust and deterministic operational timeline, we need a unified `ActivityEvent` table. The existing `PortObservationEvent` is closely tied to the `ingestion_service.py` and specifically structured around port metadata. A new `ActivityEvent` table can serve as the global append-only log for all workflow transitions (`PORT_APPEARED`, `RESERVATION_CREATED`, `HOST_OFFLINE`, etc.) providing a clean, paginated timeline for the dashboard without relying on complex and lossy dynamic derivation.
