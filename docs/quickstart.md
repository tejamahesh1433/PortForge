# Quick Start

This guide gets PortForge running for a single host, then shows how agents on
other machines must address Central.

## Trust boundary

PortForge Central and Dashboard are intended for:

- localhost
- trusted LAN
- private VPN

They are **not** designed for direct public-Internet exposure. The dashboard
does not provide browser authentication. Administrative operations (Add Host,
Remove Record) use server-side admin/bootstrap credentials. See
`docs/security.md`.

## Start Central (production-style local stack)

```bash
cp .env.example .env   # set PORTFORGE_ADMIN_BOOTSTRAP_TOKEN
docker compose up -d
```

Defaults:

| Service | Address |
|---------|---------|
| Central API | `http://127.0.0.1:58000` |
| Dashboard | `http://127.0.0.1:3000` |
| PostgreSQL | `127.0.0.1:55432` |

For **v1.3 feature development**, do **not** rebuild this stack. Use the
isolated development project instead: `docs/development-isolation.md`.

## Case A — Agent on the same machine as Central

The agent and Central share one host. Use loopback:

```bash
portforge central generate-token --url http://127.0.0.1:58000
portforge agent enroll --server http://127.0.0.1:58000 --token "<ENROLLMENT_TOKEN>"
portforge agent service install
portforge agent service start
portforge doctor --url http://127.0.0.1:58000
```

`http://127.0.0.1:58000` means “Central on **this** machine.”

## Case B — Agent on another LAN / private-network host

`127.0.0.1` on the agent machine refers to **the agent’s own machine**, not the
Central host. Another machine **cannot** enroll using the Central machine’s
localhost address.

1. On the Central host, bind the API so trusted peers can reach it (example):

```bash
PORTFORGE_API_BIND=0.0.0.0 docker compose up -d
```

2. Ensure firewall / security group allows TCP **58000** (or your configured
   Central port) from the private network / VPN only.
3. On the remote agent, use the Central host’s **private** address:

```bash
portforge agent enroll \
  --server http://<CENTRAL_PRIVATE_IP>:58000 \
  --token "<ENROLLMENT_TOKEN>"
```

Replace `<CENTRAL_PRIVATE_IP>` with the Central host’s LAN or VPN address
(discover it with your OS network tools). Do **not** hard-code a personal IP
into shared documentation.

Connectivity checklist:

- Agent can open TCP to `<CENTRAL_PRIVATE_IP>:58000`
- Central bind address is not limited to loopback if remote agents are required
- Path is private (LAN / VPN) — do not expose Central on the public Internet

## Enroll via Dashboard (Add Host)

Operators can mint enrollment tokens from the Dashboard without handing out the
admin bootstrap secret. See `docs/runbooks/add-host.md`.

## Basic scan and reservation

```bash
portforge scan
portforge next --purpose web --json
portforge reserve --port 3000 --purpose web --project my-app
portforge release --port 3000 --project my-app
```

## Project workflow (recommended)

```bash
portforge project init
portforge project validate
portforge workflow prepare
portforge workflow apply
portforge workflow status
```

## Operator runbooks

- Add Host — `docs/runbooks/add-host.md`
- Remove Record — `docs/runbooks/remove-record.md`
- Re-enrollment — `docs/runbooks/re-enrollment.md`
- Recheck status — `docs/runbooks/recheck-status.md`
- Agent services — `docs/runbooks/agent-services.md`
- Upgrade / rollback — `docs/runbooks/agent-upgrade.md`, `docs/runbooks/agent-rollback.md`
- Laptop sleep — `docs/runbooks/mac-sleep.md`
