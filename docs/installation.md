# Installation Guide

This guide covers the prerequisites and installation steps for PortForge Central and the Host Agents. Every
command below was verified directly against the real CLI (`portforge --help` and subcommand `--help`) and the
real `docker-compose.yml` -- see `docs/v1.1/install-upgrade-audit.md` for the audit that found (and this
document fixes) the previous version's defects.

## Prerequisites
- **Python**: 3.10 or higher, on every machine that runs an agent (Central host or agent-only host).
- **Docker**: Optional. Only required on the Central host if you run Central via Docker Compose, or on an agent
  host if you want PortForge to scan container bindings.
- **PostgreSQL**: Only required on the Central host (Docker Compose provisions it for you).

There is no separate `portforge-agent` binary. `pip install -e ./agent` installs exactly one console script,
`portforge` (see `agent/pyproject.toml`'s `[project.scripts]`), which is namespaced into subcommands
(`portforge agent ...`, `portforge central ...`, `portforge project ...`, etc.) -- confirm with `portforge --help`
after installing.

---

## 1. Central Setup
Central should be installed on a single machine or server in your network. Agent-only hosts (see below) do
**not** need Postgres, Docker, or the dashboard -- only the Central host does.

**Using Docker Compose (Recommended)**
```bash
git clone https://github.com/user/PortForge.git
cd PortForge
export PORTFORGE_ADMIN_BOOTSTRAP_TOKEN="choose-a-real-secret-here"  # required -- compose refuses to start without it
docker-compose up -d
```
This spins up PostgreSQL (host port `${PORTFORGE_DB_HOST_PORT:-55432}`, container-internal `5432`), the
FastAPI backend on host port `${PORTFORGE_API_HOST_PORT:-58000}` (container-internal `8000` -- the two are
often confused; the host port is what you actually connect to), and the Next.js Dashboard on
`${PORTFORGE_DASHBOARD_HOST_PORT:-3000}`. `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` is the only required variable;
keep it secret -- it is the credential that mints host enrollment tokens (step 3 below). Confirm Central is up:
```bash
curl http://localhost:58000/api/health
```

---

## 2. Host Agent Setup

The Host Agent must be installed on every machine (developer laptop, server) that you want to manage,
**including the Central host itself if you want PortForge to manage ports on that machine too.**

Install the CLI (same command on Windows/macOS/Linux -- ideally inside a virtualenv):
```bash
pip install -e ./agent
portforge --help          # confirms the install worked and the console script is on PATH
```

Install and start the native background service for your OS:
```bash
portforge agent service install
portforge agent service start
portforge agent service status   # confirm it's running
```
This creates, per platform: a Windows Task Scheduler task (`ONLOGON`, no elevation required), a macOS
per-user LaunchAgent, or a Linux `systemctl --user` service. The command is safe to re-run -- it reinstalls the
definition in place rather than creating a duplicate, and preserves your host identity and enrollment. On
Linux, install also enables `loginctl linger` for your account (when it's not already on) so the service
survives logout/reboot on a headless host; if it can't (a locked-down system may refuse this), the install
still succeeds and prints an informational note explaining the effect and what an administrator would need to
run instead.

### Firewall

PortForge never opens or disables a firewall for you. The agent only makes outbound HTTPS/HTTP requests to
your configured Central URL; nothing needs to be exposed on the agent host. Central's host port (58000 by
default) does need to be reachable *from* every agent host -- if your Central host has an inbound firewall,
opt in explicitly (e.g. `sudo ufw allow 58000/tcp`, or the Windows Firewall GUI/`New-NetFirewallRule`) rather
than disabling the firewall wholesale. The install scripts (`scripts/install.ps1`/`install.sh`) never touch
firewall configuration.

## 3. Host Enrollment
Once an agent is installed and running, enroll it with Central so it can begin reporting active ports.

First, mint a one-time enrollment token on the **Central host**, using the admin bootstrap token from step 1
(never share this token; it is shown exactly once and cannot be retrieved again):
```bash
export PORTFORGE_ADMIN_BOOTSTRAP_TOKEN="the-same-secret-from-step-1"
portforge central generate-token --url http://central-host:58000 --label my-laptop
```

Then, on the **agent machine**, enroll with that token:
```bash
portforge agent enroll --server http://central-host:58000 --token "THE_TOKEN_FROM_ABOVE"
```
Restart the native service (`portforge agent service stop` then `start`, or just `install` again) so the
running daemon picks up the new credential. Finally, verify the whole install:
```bash
portforge doctor --url http://central-host:58000
```
`doctor` is the canonical post-install check -- it is read-only (never mutates anything) and reports CLI
version, Central reachability, agent enrollment/credential state, native service status, Docker availability,
and manifest validity together in one place.

There is a second, older, one-off enrollment path (`portforge central enroll --url ... --enrollment-token ...`)
used by the Phase 5 `portforge central sync`/`status` commands. It is still real and still works, but does not
start or manage the always-on agent daemon -- use `portforge agent enroll` (above) for a normal installation.
