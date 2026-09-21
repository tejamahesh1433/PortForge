# Current Limitations

PortForge v1.0.0 represents a complete, stable implementation of autonomous port management, but there are some known limitations in this release.

## Unmanaged Process Theft
PortForge guarantees that multiple PortForge-compliant projects will never overlap. However, if an external unmanaged process (e.g., a developer manually launching `python -m http.server 3000`) binds to a port that PortForge has reserved but the actual project container hasn't started yet, a bind race can occur. PortForge detects this failure at launch time and will gracefully roll back and release the allocation, but it cannot physically prevent the external process from taking the port.

## Remote Bind-Probes
Central relies on telemetry snapshots transmitted by authenticated Host Agents. It currently does not perform out-of-band active TCP probing to remote hosts to confirm if a port is responding. If a Host Agent goes completely offline (e.g., laptop lid closed), Central will eventually flag its data as `STALE`, conservatively preserving its active reservations to prevent collisions upon its return.

## Filesystem Transactions
PortForge mutates `.env` and `docker-compose.yml` files when applying allocations. Because these are separate filesystem writes, they cannot be wrapped in a literal ACID multi-file transaction. PortForge compensates by using backup files and supporting explicit `portforge config rollback` commands to repair state in the rare event of a partial write failure.

## Kubernetes Support
PortForge v1.0.0 is primarily designed for native processes and Docker Compose development workflows. It does not currently contain native integrations or config mutators for Kubernetes manifests (`Deployment`, `Service`).

## Recommendation Plans are Advisory
A "recommendation" or "project plan" returned by Central is advisory and point-in-time. It is not locked. It must be upgraded to a Reservation and subsequently an Allocation to guarantee safety.
