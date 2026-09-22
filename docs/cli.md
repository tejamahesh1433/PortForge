# CLI Reference

The `portforge` command-line interface provides tools for discovery, reservation, configuration, and autonomous workflows.

## Basic Operations

- `portforge scan`: Forces an immediate scan of native OS and Docker port bindings, transmitting the snapshot to Central. Supports `--json`.
- `portforge check <port>`: Checks if a specific port is currently bound or reserved on the local machine.
- `portforge next`: Queries Central for an available port recommendation. 
  - Usage: `portforge next --purpose web --preferred 3000`
- `portforge reserve`: Manually creates a temporary advisory lock (reservation) on a port.
  - Usage: `portforge reserve --port 3000 --purpose web --project my-app`
- `portforge release`: Releases a manual reservation.
  - Usage: `portforge release --port 3000 --project my-app`
- `portforge reservations`: Lists all active reservations on the local host.
- `portforge conflicts`: Checks the current host state against Central's registry to identify overlapping assignments.

## Setup Commands

- `portforge central generate-token`: Mints a new host enrollment token. Admin-only -- requires the Central
  host's `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` (env var preferred over `--admin-token`, to avoid shell history
  exposure). The returned token is shown exactly once.
  - Usage: `portforge central generate-token --url "http://central:58000" --label my-laptop`
- `portforge agent enroll`: Enrolls the local agent daemon with Central using a URL and enrollment token. This
  is the command a normal installation should use (writes the credential the always-on daemon actually reads).
  - Usage: `portforge agent enroll --server "http://central:58000" --token "<token>"`
- `portforge central enroll`: An older, separate one-off enrollment path used only by `portforge central
  sync`/`status`. Still real, but does not configure the always-on agent daemon -- prefer `agent enroll` above.
- `portforge agent service install/start/stop/status/uninstall`: Controls the native background OS service for
  the host agent (Windows Task Scheduler / macOS LaunchAgent / Linux `systemctl --user`). `install` is
  idempotent -- safe to re-run after an upgrade or config change.
- `portforge doctor`: Read-only diagnostic check of CLI/Central/agent/manifest state -- the canonical
  post-install verification gate. Never mutates anything. Supports `--url` and `--json`.
- `portforge agent-contract`: Prints the JSON schema detailing the rules of engagement for AI Coding Agents.

## Advanced Allocations

- `portforge allocate`: Manually promotes a reservation into an allocation.
- `portforge allocation get <request_id>`: Retrieves details of an existing allocation.
- `portforge allocation release <request_id>`: Releases an entire allocation block, freeing all ports.

## Project & Manifest Commands

- `portforge project init`: Creates a boilerplate `portforge.yml` manifest in the current directory.
- `portforge project validate`: Validates the syntax and structure of the `portforge.yml` manifest.
- `portforge project plan`: Calculates port recommendations for the entire project without locking them.
- `portforge project allocate`: Bypasses the workflow process and directly allocates ports for a manifest (not recommended for automated agents).

## Configuration Commands

- `portforge config plan`: Previews the mutations that will be made to `.env` and `docker-compose.yml`.
- `portforge config apply`: Writes the allocated ports into the project's configuration files.
- `portforge config status`: Checks if the current configuration files match the active allocation.
- `portforge config rollback`: Restores configuration files to their previous state using backups.

## Autonomous Workflow Commands

These commands combine project planning, reservations, allocations, and config mutation into idempotent, crash-safe operations.

- `portforge workflow prepare`: Submits the `portforge.yml` to Central, generating a `request_id` and locking advisory reservations.
- `portforge workflow apply`: Promotes the reservations into allocations and automatically executes `config apply`.
- `portforge workflow status`: Retrieves the current completion status of the workflow.
