# Quick Start

This guide will get you up and running with PortForge, demonstrating how to allocate ports safely for a new project.

## 1. Start Central and Enroll

Assuming Central is running (via Docker Compose, on host port 58000 by default -- see
`docs/installation.md`), enroll your host agent to begin scanning for active ports:

```bash
# Mint an enrollment token (needs the Central host's admin bootstrap token)
export PORTFORGE_ADMIN_BOOTSTRAP_TOKEN="your-admin-bootstrap-token"
portforge central generate-token --url http://localhost:58000
# Output includes: "enrollment_token": "..." -- shown once, save it

# Enroll the local machine
portforge agent enroll --server http://localhost:58000 --token "THE_TOKEN_FROM_ABOVE"
```

## 2. Trigger a Scan

Force the agent to report the current state of host ports:
```bash
portforge scan
```

## 3. Basic Reservation (Manual)

If you just want to grab a port manually:
```bash
# Ask Central for an available port recommendation
portforge next --purpose web --json
# Output: {"port": 3000, ...}

# Reserve it
portforge reserve --port 3000 --purpose web --project my-app

# When you're done, release it
portforge release --port 3000 --project my-app
```

## 4. Project Workflow (Recommended)

The most powerful way to use PortForge is via the declarative project workflow.

1. Navigate to your project directory.
2. Initialize PortForge for the project:
   ```bash
   portforge project init
   ```
   This creates a `portforge.yml` manifest. Edit it to define the ports your project needs:
   ```yaml
   version: 1
   project: my-awesome-app
   ports:
     frontend:
       purpose: http
       protocol: tcp
     database:
       purpose: db
       protocol: tcp
   ```

3. Validate your manifest:
   ```bash
   portforge project validate
   ```

4. Prepare (Reserve) the ports:
   ```bash
   portforge workflow prepare
   ```

5. Apply (Allocate and Write Configs):
   ```bash
   portforge workflow apply
   ```
   PortForge will contact Central, lock the ports, write the assigned ports to your `.env` file (e.g., `PORT_FRONTEND=3000`, `PORT_DATABASE=5432`), and finalize the allocation in the registry.

6. Check Status:
   ```bash
   portforge workflow status
   ```

You are now ready to run `docker compose up` or start your local dev server knowing your ports are safe from collision!
