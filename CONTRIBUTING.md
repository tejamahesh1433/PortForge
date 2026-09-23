# Contributing to PortForge

Thank you for your interest in contributing to PortForge!

## Development Setup

PortForge consists of three primary components:
1. **Agent** (Python)
2. **Central API Backend** (Python)
3. **Dashboard** (Next.js/TypeScript)

### Python Requirements
- **Python:** 3.12 or 3.13 is recommended.
- A virtual environment is required for clean development (stdlib `venv` or `uv`).

### Canonical monorepo bootstrap (repository root)

Backend source imports the agent package (`portforge_agent`) — see
`backend/app/models/base.py`. Installing `backend/requirements-dev.txt` alone is
**not** sufficient: that file lists only backend third-party/test dependencies.

From the **repository root** (the directory containing `agent/` and `backend/`):

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
#   .venv\Scripts\activate

python scripts/bootstrap_dev.py
```

That single bootstrap command installs:

1. `./agent` into the active environment (non-editable — preferred for clean
   validation)
2. `backend/requirements-dev.txt` (pytest, httpx, and backend runtime deps)

For day-to-day agent editing you may instead use:

```bash
python scripts/bootstrap_dev.py --editable
```

Do **not** rely on a sibling-path entry such as `-e ../agent` inside
`backend/requirements-dev.txt`. That form is intentionally absent so installs
do not depend on how pip resolves relative paths from different CWDs.

### Dashboard (Node)

- **Node.js:** v20+ recommended.
- **Package Manager:** `npm`.

```bash
cd dashboard
npm install
```

## Running Tests

Please ensure tests pass before submitting changes. Activate the same venv
created above first.

### Backend

```bash
cd backend
python -m pytest
```

### Agent

```bash
cd agent
python -m pytest
```

### Dashboard

```bash
cd dashboard
npm run test
```

## Linting, Typechecking, and Building

For the frontend dashboard:

```bash
cd dashboard
npm run lint
npm run typecheck
npm run build
```

## Security & Commit Expectations

**CRITICAL RULE:** Do **NOT** commit any of the following to the repository:
- Passwords or authentication tokens.
- SSH private keys.
- Host identity files (`identity.json`).
- Enrollment secrets or `.env` files with real credentials.
- Private infrastructure information (real hostnames, internal IPs, or specific user paths from your organization).

If your tests require fixtures, use generic names (e.g. `server-a`, `198.51.100.2`, `user`).

## Cross-Platform Expectations

PortForge explicitly supports **Windows**, **macOS**, and **Linux**. Any changes to the `agent` component must respect cross-platform compatibility. Do not assume POSIX paths or tools on Windows, and do not assume Windows-specific tools (like PowerShell) on Linux/macOS.
