# Scripts

Cross-platform helpers for PortForge development and operations.

| Script | Purpose |
|---|---|
| `bootstrap_dev.py` | **Canonical monorepo bootstrap** (repository root): installs `./agent` then `backend/requirements-dev.txt` into the active venv |
| `install.sh` / `install.ps1` | Host agent production install + OS service registration (see `docs/installation.md`) |
| `check_secrets.sh` / `check_secrets.ps1` | Local gitleaks scans |

## Developer bootstrap

From the repository root, with a virtualenv activated:

```bash
python scripts/bootstrap_dev.py
```

See `CONTRIBUTING.md` for the full clean-development procedure and test commands.
