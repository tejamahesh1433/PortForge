# PortForge Manifest (`portforge.yml`)

The `portforge.yml` file is the declarative manifest used by the PortForge CLI to compute, reserve, and allocate ports for a specific project.

**Important Note**: Do NOT confuse `portforge.yml` with the legacy `.portforge.yml`. The legacy hidden file belonged to older, local-only reservation-sync semantics and must not be repurposed as the project manifest. The v1.0.0 CLI strictly relies on `portforge.yml`.

## Schema Overview

```yaml
version: 1
project: <string>
target:
  host: <string>            # (Optional) Target hostname. Defaults to local machine.
ports:
  <port_id>:                # E.g. "frontend", "db"
    purpose: <string>       # Broad category (e.g., http, db, cache)
    protocol: <string>      # "tcp" or "udp"
    preferred: <integer>    # (Optional) Preferred port number
request_id: <string>        # (Auto-generated) Used for idempotency
config:
  dotenv: <string>          # (Optional) Target .env file to mutate. Default: ".env"
  compose: <string>         # (Optional) Target compose file to mutate. Default: "docker-compose.yml"
```

## Field Details

- `version`: Must be `1`.
- `project`: Unique identifier for your project stack (e.g., `my-nextjs-app`).
- `target.host`: Optional explicit target. If omitted, PortForge defaults to the enrolled hostname of the machine executing the CLI.
- `ports`: A dictionary of logical port definitions.
  - `<port_id>`: The internal reference key (e.g., `web`, `postgres`). This determines the generated `.env` variable name (e.g. `PORT_WEB`, `PORT_POSTGRES`).
  - `purpose`: Helps the recommendation engine distribute workloads intelligently.
  - `preferred`: Strongly hints to the recommendation engine to attempt to secure this port first. It is not guaranteed.
- `request_id`: Generated automatically by `portforge workflow prepare`. Do not alter this manually. It ensures your operations are idempotent and resistant to connection drops.
- `config`: Configuration for automatic file mutation.
  - `dotenv`: Path to the environment file. PortForge will append/update variables like `PORT_<PORT_ID>=<ALLOCATED_PORT>`.
  - `compose`: Path to the docker-compose file. PortForge will attempt to locate placeholders and inject the allocated ports.

## Complete Example

```yaml
version: 1
project: ecommerce-platform
target:
  host: workstation
ports:
  frontend:
    purpose: http
    protocol: tcp
    preferred: 3000
  api:
    purpose: http
    protocol: tcp
    preferred: 8000
  db:
    purpose: db
    protocol: tcp
    preferred: 5432
config:
  dotenv: .env.local
  compose: docker-compose.dev.yml
```
