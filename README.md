# PortForge

PortForge is a centralized host-aware port discovery, reservation, allocation, and configuration management system.

## The Core Problem
Multiple local projects, containers, developers, servers, and coding agents can independently choose host ports and collide. In complex microservice or multi-host environments, manually managing port assignments in `.env` or `docker-compose.yml` files leads to conflicts, leaked resources, and broken workflows.

PortForge solves this by acting as the single source of truth for port authority.

## Key Capabilities
- **Centralized Port Registry**: Tracks active bindings, reservations, and allocations across your entire fleet.
- **Autonomous Agent Integration**: Coding agents (like Antigravity) use PortForge to dynamically request safe ports without guessing or hardcoding.
- **Cross-Host Intelligence**: Manages port states across different OSes and physical machines in your network.
- **Idempotency & Safety**: Safe against crashes, network partitions, and process interruptions. Rollbacks ensure no leaked allocations.
- **Dashboard**: A comprehensive UI for visualizing port topologies, conflicts, and host health.

## Architecture Overview
PortForge relies on a Central FastAPI server backed by PostgreSQL, paired with distributed Host Agents.

```text
Host Agents (Scan/Probe)
        ↓
Central API (FastAPI)
        ↓
PostgreSQL (Registry)

Dashboard → Central API
Coding Agent → PortForge CLI → Central API
```

## Supported Operating Systems
- **Windows**: Full support (Native, Docker)
- **macOS**: Full support (Native, Docker)
- **Linux**: Full support (Native, Docker)

## Quick Start
Check out the [Quick Start Guide](docs/quickstart.md) for a rapid introduction on how to initialize your first project and request an allocation.

## Documentation
- [Installation Guide](docs/installation.md)
- [Architecture Details](docs/architecture.md)
- [Coding-Agent Integration](docs/coding-agents.md)
- [Manifest (`portforge.yml`) Schema](docs/manifest.md)
- [CLI Reference](docs/cli.md)
- [Security Model](docs/security.md)
- [Current Limitations](docs/limitations.md)

## Development and Testing
PortForge is thoroughly tested with comprehensive Pytest suites for both the backend and agent CLI. Please refer to [Contributing](CONTRIBUTING.md) if you wish to run the test matrix or contribute.