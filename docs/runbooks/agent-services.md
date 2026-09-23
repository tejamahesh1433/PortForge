# Runbook: Agent service management

PortForge manages a native per-OS service via the agent CLI. Commands are
identical across platforms; backends differ.

## Supported commands

```bash
portforge agent service install
portforge agent service start
portforge agent service stop
portforge agent service status
portforge agent service uninstall
```

There is **no** `portforge agent service restart` command. Restart with:

```bash
portforge agent service stop
portforge agent service start
```

Optional: append `--json` for machine-readable output.

## Platform backends

| OS | Mechanism |
|----|-----------|
| Windows | Task Scheduler task `PortForge Agent` |
| macOS | LaunchAgent `com.portforge.agent` |
| Linux | user systemd unit `portforge-agent.service` |

If an operation fails due to OS permissions or policy, use the corresponding
native tooling (Task Scheduler / `launchctl` / `systemctl --user`) to inspect
logs — PortForge does not replace those tools.

## Typical first-time setup

```bash
pip install -e ./agent
portforge agent enroll --server http://<CENTRAL_ADDRESS>:<PORT> --token "<ENROLLMENT_TOKEN>"
portforge agent service install
portforge agent service start
portforge doctor --url http://<CENTRAL_ADDRESS>:<PORT>
```

## Related

- `docs/installation.md`
- `docs/cli.md`
- `docs/runbooks/agent-upgrade.md`
