# Contributing to PortForge

Thank you for your interest in contributing to PortForge!

## Development Setup

PortForge consists of three primary components:
1. **Agent** (Python)
2. **Central API Backend** (Python)
3. **Dashboard** (Next.js/TypeScript)

### Python Requirements
- **Python:** 3.12 or 3.13 is recommended.
- **Package Manager:** `uv` is heavily recommended for managing virtual environments and dependencies.
- **Installation:**
  ```bash
  # Agent
  cd agent
  uv venv
  # on Linux/Mac: source .venv/bin/activate
  # on Windows: .venv\Scripts\activate
  uv pip install -e .[dev]

  # Backend
  cd backend
  uv venv
  # activate as above
  uv pip install -r requirements.txt
  ```

### Node Requirements
- **Node.js:** v20+ recommended.
- **Package Manager:** `npm`.
- **Installation:**
  ```bash
  cd dashboard
  npm install
  ```

## Running Tests

Please ensure tests pass before submitting changes.

### Agent
```bash
cd agent
python -m pytest
```

### Backend
```bash
cd backend
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
