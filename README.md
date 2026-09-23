# PortForge

PortForge is a cross-platform fleet-aware port management system. 

It solves the problem of local port collisions across diverse developer and deployment environments. When managing infrastructure on multiple machines, tracking which ports are available and which are occupied can become chaotic. PortForge acts as a central registry and agent system that intelligently discovers, reserves, and manages port allocations safely across your entire fleet.

## Features

- **Cross-Platform:** Full support for Windows, macOS, and Linux.
- **Agent System:** Lightweight agents on each host track bound ports and Docker/Compose usage.
- **Central Registry:** A PostgreSQL-backed central API maintains the global state of the fleet.
- **Port Discovery:** Automatically discovers running services and open ports on each host.
- **Docker/Compose Awareness:** Analyzes running containers to determine bound ports.
- **Reservations:** Request a port, and PortForge will allocate an available port globally, handling conflicts.
- **Dashboard:** A clean web interface to visualize fleet health, active ports, and allocations.
- **Kubernetes & Remote Host Support:** Designed for both local processes and external orchestrators.

## Architecture

PortForge consists of three main components:
1. **Agent:** Runs on each machine (laptop, server, cloud VM). It periodically probes the system and synchronizes its state with the Central API.
2. **Central API:** The backend brain. It tracks all agents, processes heartbeats, handles reservation requests, and stores the source of truth in PostgreSQL.
3. **Dashboard:** A Next.js frontend to view your fleet's state and manage reservations.

## Installation & Quick Start

See **[docs/quickstart.md](docs/quickstart.md)** for localhost vs LAN enrollment
(Case A / Case B). Summary:

### Local-Only Deployment (same machine)

```bash
cp .env.example .env   # set PORTFORGE_ADMIN_BOOTSTRAP_TOKEN
docker compose up -d
cd agent && pip install -e .
portforge agent enroll --server http://127.0.0.1:58000 --token "<ENROLLMENT_TOKEN>"
portforge doctor --url http://127.0.0.1:58000
```

Defaults: API `127.0.0.1:58000`, Dashboard `127.0.0.1:3000`, PostgreSQL `127.0.0.1:55432`.

### Multi-Host / LAN Deployment

Remote agents must use the Central host’s **private IP** (or VPN address), not
`127.0.0.1` — localhost on the agent means the agent machine itself.

```bash
PORTFORGE_API_BIND=0.0.0.0 docker compose up -d
# On the remote host:
portforge agent enroll --server http://<CENTRAL_PRIVATE_IP>:58000 --token "<ENROLLMENT_TOKEN>"
```

Allow TCP 58000 only on the trusted network. **Do not expose PortForge on the public Internet.**

### v1.3 feature development

Do **not** run `docker compose up --build` against the production project while
developing. Use the isolated stack:

- Project `portforge-dev` — API `:58001`, Dashboard `:3001`, DB `:55433`
- Guide: [docs/development-isolation.md](docs/development-isolation.md)

Operator runbooks (Add Host, Remove Record, services, upgrade): [docs/runbooks/](docs/runbooks/).

## Security Considerations

- **Trust boundary:** localhost / trusted LAN / private VPN only. No browser authentication.
- **Database:** PostgreSQL defaults to `127.0.0.1`. Agents talk to the API, not the DB.
- **Admin secret:** Dashboard BFF routes keep `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` server-side only.
- See [SECURITY.md](SECURITY.md) and [docs/security.md](docs/security.md).

## Development & Testing

See [CONTRIBUTING.md](CONTRIBUTING.md) for bootstrap, tests, and the `portforge-dev` stack.

## Current Status

PortForge is currently actively maintained and production-ready for private fleet management.
## License

PortForge is licensed under the [Apache License 2.0](LICENSE).
