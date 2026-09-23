# Runbook: Add Host

Use this when enrolling a new machine into PortForge Central via the dashboard.

## Prerequisites

- Central and Dashboard reachable on a trusted network (localhost / LAN / VPN).
- Dashboard process has `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` configured **server-side**
  (never `NEXT_PUBLIC_*`).
- The target machine can reach Central over TCP (default Central port **58000**
  for production, **58001** for the `portforge-dev` stack).

## Workflow

1. Open the Hosts page in the Dashboard.
2. Choose **Add host**.
3. Optionally set a short label and token lifetime (hours).
4. Generate the enrollment token.

Architecture:

```text
Browser
  → Dashboard POST /api/enrollment-tokens
      → (server attaches admin/bootstrap credential)
          → Central POST /api/agent/enrollment-tokens
```

The browser never receives the admin/bootstrap secret. The enrollment token is
returned only for the enrollment workflow.

5. On the target machine, enroll with placeholders (never paste real tokens into docs):

```bash
portforge agent enroll \
  --server http://<CENTRAL_ADDRESS>:<PORT> \
  --token "<ENROLLMENT_TOKEN>"
```

6. Install and start the agent service if it is not already managed:

```bash
portforge agent service install
portforge agent service start
portforge doctor --url http://<CENTRAL_ADDRESS>:<PORT>
```

## Credential semantics

| Secret | Lifetime | Purpose |
|--------|----------|---------|
| Admin / bootstrap token | Long-lived server secret | Mint enrollment tokens; dashboard BFF admin calls |
| Enrollment token | Short-lived, one-time | Enroll a host once |
| Agent credential | Long-lived per host | Heartbeat / sync after enrollment |

The enrollment token is **not** the long-term agent credential. After a successful
enroll, Central issues a host-specific credential stored locally by the agent.

## Failure notes

- Missing dashboard admin configuration → enrollment mint fails safely (no secret leak).
- Central unreachable → dashboard reports a connection failure.
- Replayed enrollment token → rejected by Central.

## Related

- `docs/runbooks/re-enrollment.md`
- `docs/development-isolation.md`
- `docs/security.md`
