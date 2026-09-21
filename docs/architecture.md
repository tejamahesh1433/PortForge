# Architecture

PortForge v1.0.0 is designed as a centralized port registry with distributed agents, ensuring port collisions are avoided both locally and across your fleet.

## Core Components

### Central API (FastAPI)
The brain of the operation. It exposes REST endpoints for hosts to report their state and for clients to request allocations. It maintains a persistent record of all hosts, port bindings, and reservations in PostgreSQL.

### Registry (PostgreSQL)
The source of truth. It stores:
- **Hosts**: Enrolled machines and their health.
- **Bindings**: Actively observed ports in use.
- **Reservations**: Temporary holds on ports requested via the CLI.
- **Allocations**: Committed assignments of ports to specific projects.

### Host Agents
Background services running on each enrolled machine (Windows, macOS, Linux).
They continuously run:
- **OS Collectors**: Interrogate `netstat`, `ss`, or platform APIs for native port bindings.
- **Docker Collector**: Scans Docker daemon for container ports and mappings.

### PortForge CLI
The tool developers and coding agents use to interact with Central. It handles:
- Project manifest (`portforge.yml`) parsing.
- Requesting recommendations and reservations.
- Committing allocations.
- Applying configuration mutations (`.env` and `docker-compose.yml`).

### Dashboard
A Next.js UI providing a visual topology of all hosts, projects, conflicts, and recent activity.

## Key Concepts

### Host Identity vs. Binding Identity
Each machine is enrolled with a unique UUID. This ensures that port `3000` on `Host-A` is tracked distinctly from port `3000` on `Host-B`. A binding is the combination of Host UUID, Port, and Protocol (TCP/UDP).

### Host Port vs. Container Port
PortForge manages **host ports**. A container may internally bind to `5432`, but PortForge allocates and tracks the *external host port* (e.g., `5435`) mapped to it.

### Recommendation & Advisory Locking
When an agent wants a port, it asks the Recommendation Engine. The engine checks current bindings and active reservations. Once a recommendation is accepted, an **Advisory Lock (Reservation)** is placed in the database. This prevents concurrency races if two agents request ports simultaneously.

### The Allocation Transaction
A Reservation is temporary (time-to-live). To finalize it, the CLI performs a `workflow apply`, mutating the project config (e.g. `.env`) and committing the Reservation into a permanent **Allocation**.

### Idempotency and Recovery
Requests use a unique `request_id`. If a network drop or crash occurs mid-workflow, retrying the exact same request simply returns the previously secured allocation without leaking resources.

### Config Mutation
The CLI applies ports directly into `.env` files and `docker-compose.yml` configs via AST or robust regex/yaml parsers, ensuring developers don't have to manually orchestrate port numbers.
