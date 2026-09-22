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

### Local-Only Deployment

To run the Central API and Dashboard on your local machine using Docker Compose:

1. Clone the repository.
2. Ensure Docker and Docker Compose are installed.
3. Start the central stack:
   ```bash
   docker compose up -d
   ```
   *Note: By default, the API binds to `127.0.0.1:58000`, Dashboard to `127.0.0.1:3000`, and PostgreSQL to `127.0.0.1:55432`.*

4. Install the Agent:
   ```bash
   cd agent
   pip install -e .
   portforge doctor
   ```

### Multi-Host / LAN Deployment

To connect agents from other machines on your LAN (or VPN), you need to expose the Central API. 
Set the bind environment variables to `0.0.0.0` before running `docker compose up`:

```bash
PORTFORGE_API_BIND=0.0.0.0 PORTFORGE_DASHBOARD_BIND=0.0.0.0 docker compose up -d
```
*Warning: Do not expose unauthenticated services directly to the public internet.*

For external agents, configure the central URL via a `.env` file or environment variables to point to your Central API's LAN/VPN address.

## Security Considerations

- **Database:** PostgreSQL is always bound to `127.0.0.1` by default. Agents interact with the API, not the database directly. Do not expose the database port to the LAN unless absolutely necessary.
- **Authentication:** The Dashboard and Central API currently do not feature browser/API authentication. They are intended for use on trusted networks (e.g., localhost, VPN, Tailscale, WireGuard).
- See [SECURITY.md](SECURITY.md) for more details.

## Development & Testing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed setup and testing instructions.

## Current Status

PortForge is currently actively maintained and production-ready for private fleet management.