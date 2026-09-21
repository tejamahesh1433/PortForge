# Phase 7C.2 Health Audit

## Existing Semantics
- **Host Model (`app/models/host.py`)**: Stores `first_seen`, `last_seen`, `status` (defaults to `"online"`), `docker_available`, `agent_version`, and `last_scan_observed_at`.
- **Status Updates (`app/services/host_service.py`)**: Upserts the host record on every heartbeat. It updates `last_seen`. It checks if `status != "online"` to emit a `HOST_ONLINE` activity event. However, there is no mechanism to set the status to "offline".
- **Snapshot Ingestion (`app/services/ingestion_service.py`)**: Processes ports. Updates `last_scan_observed_at`.
- **Health API (`app/api/health.py`)**: Currently very lightweight, primarily checking database connectivity.
- **Config (`app/config.py`)**: No existing health freshness thresholds.

## Missing Information
- No precise definition or configuration for `STALE` or `OFFLINE` thresholds.
- No `snapshot_age_seconds` or `age_seconds` exposed in the API.
- No diagnostic reason codes explaining *why* a host is in a specific state.
- No visibility into version mismatch across the fleet.
- Central doesn't track its own metrics (e.g., active host counts, latest ingestion time) in `/api/health`.

## Safe Derivation Strategy
We can calculate health states *on-the-fly* in the API response (e.g., `HostOut` schema) based on `last_seen` and `last_scan_observed_at` compared to `datetime.now(timezone.utc)`. 

States:
- **HEALTHY**: `age <= STALE_THRESHOLD`
- **STALE**: `STALE_THRESHOLD < age <= OFFLINE_THRESHOLD`
- **OFFLINE**: `age > OFFLINE_THRESHOLD`

The API endpoints will return these derived states along with reason codes (e.g., `AGENT_HEALTHY`, `AGENT_SYNC_STALE`, `AGENT_OFFLINE`).

## Agent Limitations & HOST_OFFLINE Event Deferral
Since there is no background scheduler to actively poll and mark hosts offline exactly when they cross the threshold, we cannot easily generate a reliable `HOST_OFFLINE` activity event at the exact moment of failure without introducing a "large distributed scheduler". 

Therefore, we will:
1. Defer persistent `HOST_OFFLINE` event generation, per the user's instructions.
2. The Dashboard will still correctly display the host as OFFLINE based on the API's on-the-fly time derivation.
3. When an agent recovers (sends a heartbeat after being OFFLINE), `record_heartbeat` will calculate its prior state based on the threshold and correctly emit a `HOST_ONLINE` recovery event (exactly once).

## Docker Health
`docker_available` is simply a boolean indicating if the Docker collector succeeded. We will not classify a host as unhealthy just because Docker is unavailable, but we will display its availability prominently.
