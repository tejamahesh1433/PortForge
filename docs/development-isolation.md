# Development isolation (v1.3+)

PortForge keeps a **frozen production** stack and a **disposable development**
stack as separate Docker Compose projects. Mixing them caused a production
incident during v1.3 Phase 2; this document exists so that cannot happen again.

## Production (do not recreate for feature work)

| Resource | Value |
|----------|-------|
| Compose project | `portforge` |
| Compose file | `docker-compose.yml` |
| Central API | `127.0.0.1:58000` (or LAN bind if intentionally configured) |
| Dashboard | `127.0.0.1:3000` |
| PostgreSQL host port | `127.0.0.1:55432` |
| Database container | `portforge-postgres` |
| Database name | `portforge` |

Qualified frozen images (example for v1.2.0):

- `portforge-api:v1.2.0-667f084`
- `portforge-dashboard:v1.2.0-667f084`

**Forbidden during v1.3 feature work:**

```bash
# DO NOT run against the production project while developing v1.3
docker compose up --build
docker compose -f docker-compose.yml up --build
```

## Development (`portforge-dev`)

| Resource | Value |
|----------|-------|
| Compose project | `portforge-dev` (fixed via `name:` in compose file) |
| Compose file | `docker-compose.dev.yml` |
| Env file | `.env.dev` (from `.env.dev.example`) |
| Central API | `127.0.0.1:58001` |
| Dashboard | `127.0.0.1:3001` |
| PostgreSQL | `127.0.0.1:55433` |
| Database name | `disposable` |
| Volume | `portforge-dev-postgres-data` (separate from production) |
| API container | `portforge-dev-api` |
| Dashboard container | `portforge-dev-dashboard` |
| Postgres container | `portforge-dev-postgres` |

Default WIP image tags:

- `portforge-api:v1.3-phase2-wip`
- `portforge-dashboard:v1.3-phase2-wip`

The development API environment hard-wires `PORTFORGE_DB_HOST=portforge-dev-postgres`
and database name `disposable`. It cannot be pointed at `portforge-postgres`
through normal `.env.dev` overrides.

## Start the development stack

```bash
cp .env.dev.example .env.dev
# edit .env.dev and set PORTFORGE_DEV_ADMIN_BOOTSTRAP_TOKEN

# If an older informal disposable Postgres already owns :55433, stop it first
# (never stop portforge-postgres):
#   docker stop portforge-disposable-db

# If a leftover local uvicorn already owns :58001, stop that process first
# (check with: netstat / ss / lsof). Docker will otherwise start the API
# container without a published host port.

docker compose -p portforge-dev -f docker-compose.dev.yml --env-file .env.dev up -d
```

Verify isolation:

```bash
curl -sS http://127.0.0.1:58001/api/diagnostics   # development
curl -sS http://127.0.0.1:58000/api/diagnostics   # production (unchanged)

# Development should expose Remove Record (admin auth required → 401 without token)
curl -sS -o /dev/null -w "%{http_code}\n" -X DELETE \
  http://127.0.0.1:58001/api/hosts/00000000-0000-0000-0000-000000000000

# Frozen production should NOT (method not allowed → 405)
curl -sS -o /dev/null -w "%{http_code}\n" -X DELETE \
  http://127.0.0.1:58000/api/hosts/00000000-0000-0000-0000-000000000000
```

Dashboard:

- Development UI: http://127.0.0.1:3001
- Production UI: http://127.0.0.1:3000

Both may run at the same time.

## Stop development only

```bash
docker compose -p portforge-dev -f docker-compose.dev.yml --env-file .env.dev down
# Add -v only when you intentionally wipe the disposable database volume.
```

## Building fresh WIP images (optional)

When you need images built from the current branch working tree, build into
**development tags** — never retag production `latest` casually:

```bash
docker build -f backend/Dockerfile -t portforge-api:v1.3-dev .
docker build -f dashboard/Dockerfile -t portforge-dashboard:v1.3-dev ./dashboard

# Then point .env.dev at those tags:
# PORTFORGE_DEV_API_IMAGE=portforge-api:v1.3-dev
# PORTFORGE_DEV_DASHBOARD_IMAGE=portforge-dashboard:v1.3-dev
```

## Trust boundary reminder

Development and production stacks are both intended for localhost / trusted LAN /
private VPN only. Neither stack provides browser authentication. See
`docs/security.md` and the operator runbooks under `docs/runbooks/`.
