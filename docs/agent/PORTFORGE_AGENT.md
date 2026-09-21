# PortForge Agent Instructions

1. Detect `portforge` with `portforge --help`; use `python -m portforge_agent` only as an installation fallback.
2. Read `portforge agent-contract --json` and require `contract_version: 1`.
3. Find `portforge.yml`; if absent, use `portforge project init` with explicit project, host, and port declarations. Never overwrite a manifest.
4. Run `portforge project validate --json`, then `portforge workflow prepare --json`.
5. Choose and persist a stable request ID. Run `portforge workflow apply --request-id <id> --json` and consume only returned ports.
6. After interruption, run `portforge workflow status --request-id <id> --project-root <dir> --json`; retry unchanged input with the same ID.
7. If cleanup is needed, rollback an applied mutation before releasing its allocation, using the returned recovery commands.

Do not inspect PortForge databases, guess ports, execute manifest content, or add undeclared config mappings.
