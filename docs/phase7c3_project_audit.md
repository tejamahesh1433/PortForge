# Phase 7C.3 Project Model Audit

## Current identity

PortForge does not persist a Project entity. Project identity is the optional `project_name` string attached to each current port observation and the `project` string attached to each reservation. Central currently groups exact-equal project names across hosts.

This means project identity is **not globally authoritative**. Two unrelated workloads that independently report the same name cannot currently be distinguished. Phase 7C.3 preserves this behavior and uses the exact project name as the URL identifier via normal URL encoding. It does not invent a UUID or silently claim stronger identity.

## Detection sources

Native process detection walks upward from the process working directory within a bounded depth. Evidence priority is:

1. Docker Compose project label (handled before filesystem detection)
2. explicit `.portforge.json` / `.portforge.yml` project
3. declared manifest name (`package.json`, `pyproject.toml`, Cargo, Go, Composer, Maven)
4. nearest Git root directory name
5. nearest recognized project marker directory
6. unknown

Detection also persists `detection_confidence`, while detailed evidence text remains agent-side and is not sent to Central.

## Compose semantics

Docker Compose project labels are authoritative. `docker_compose_project` and `project_name` retain the Compose project, while service/container/image and host-port to container-port mappings remain on each physical binding. EXPOSE-only ports never enter current observations.

## Native process semantics

Native bindings receive project context from bounded filesystem evidence rooted at the owning process working directory. PID identity is host-scoped; neither PID nor port is globally unique.

## Multi-host semantics

A project name can appear on Windows, macOS, Linux, and multiple hosts. Central aggregates the project context while every binding and reservation retains `host_id`. The same numeric port on different hosts is valid and is not a conflict.

## Reservations and conflicts

Reservations use a free-form project string and are host-scoped. A reservation-only project is valid. Conflict evaluation compares a reservation with the current observation for the same host, port, protocol, and bind address. Cross-host port equality is irrelevant.

## Activity

Reservation activity stores project in `identity_context`. Existing port activity often stores process/container identity instead, so historical project linkage is incomplete. Phase 7C.3 stores `project_name` in event metadata for new port events and only returns activity with reliable project linkage; it does not retrofit old events.

## Freshness

Host freshness is authoritative from Phase 7C.2 and remains host-specific. A mixed-health project is not flattened into a single current/stale claim. Current observations are preserved when a host becomes stale/offline.

## API limitations before 7C.3

- `GET /api/projects` performed per-project and per-binding lookups.
- Project list returned large embedded entry arrays without reservation/conflict/health summaries.
- No exact project detail endpoint existed.
- No reservation-only projects appeared.
- Activity had no project filter.
- Project-name collisions could not be disambiguated.
- Detailed native detection evidence was unavailable centrally.

## Phase 7C.3 identity decision

Use exact project name as the current identifier and URL-encode it. This is stable for the lifetime of current observations/reservations and compatible with existing data. A future identity migration requires new agent evidence (for example explicit workspace IDs) and is intentionally deferred.
