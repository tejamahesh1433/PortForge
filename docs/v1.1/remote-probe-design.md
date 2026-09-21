# v1.1 Design: Authoritative Remote Bind Probing

**Design only — not implemented in this task.**

## Why this exists

v1.0 is honest that it cannot do this: `bind_probe` is always
`"not_remote_capable"` in every allocation response
(`docs/phase8a_agent_allocation.md` "Remote-host validation limitations"),
and `CentralRecommendationOut.verification` is always `"central_suggestion"`,
never `"locally_verified"` — a field Phase 5 deliberately reserved for
exactly this future work, specifically so adding it wouldn't require an API
shape change (`backend/app/schemas/recommendation.py`'s own docstring says
this outright). v1.1 is the first phase positioned to actually populate
that field.

**Non-negotiable constraint carried forward from v1.0**: this must
*improve confidence*, never *claim a guarantee the architecture can't
back*. A probe result is a point-in-time observation, not a lock.

## What exists today (the transport this must build on)

- Central has **no outbound channel to any agent**. Every request is
  agent-initiated: enroll once, then heartbeat every 30s and a discovery
  scan every 15s (`runtime/agent.py`), each independently authenticated
  via a per-host bearer token (`require_agent`, established Phase 5/6).
- Central cannot open a connection to an agent — agents are frequently
  behind NAT/firewalls with no listening port of their own. **Any
  Central→agent communication must be pull-based**, delivered on the
  agent's own next heartbeat, not pushed.
- The agent already has a real, local, `bind()`-then-immediately-`close()`
  probe (`bindprobe.py`, used today by `portforge check`) — the remote
  case only needs a transport to REQUEST that same local capability from
  a specific remote host and carry the answer back, not a new probing
  mechanism.

## Design: queued probe request, delivered and answered on heartbeat

```
Central                                  Remote Agent (e.g. lenovoserver)
  |  probe request queued for host X          |
  |  (created by an allocation/recommendation  |
  |   call, or directly via a new endpoint)    |
  |                                             |
  |<---- heartbeat (every 30s, existing) -------|
  |---- pending probe(s) for this host -------->|  (piggy-backed on the
  |                                             |   existing heartbeat
  |                                             |   response body)
  |                                             |  agent runs bindprobe.py
  |                                             |  locally, immediately
  |<---- probe result(s) ----------------------|  (next heartbeat or a
  |      {port, protocol, available, at}        |   fast follow-up call,
  |  Central stores result, keyed by            |   both already
  |  (host_id, port, protocol), with a          |   authenticated via the
  |  freshness TTL                              |   same bearer token)
```

This is **option C+D** from the four evaluated below — a heartbeat-borne
command queue (C) with a short-lived result lease (D). It reuses the
existing authenticated transport entirely: no new agent-listening port,
no new credential type, no change to the fact that Central never
initiates a connection.

### Options evaluated

| Option | Verdict |
|---|---|
| **A. Synchronous remote probe** (caller blocks until the agent answers) | Rejected as the default. Worst-case latency is bounded by the *heartbeat interval* (30s), not the probe itself (near-instant) — a `workflow apply` call blocking up to 30s for one port, or longer for a multi-port bundle probed serially, is a poor interactive experience. Could be offered later as an explicit opt-in (`--wait-for-probe <timeout>`) once the async path is proven, not as the default. |
| **B. Queued probe request** (Central records "I asked," no delivery mechanism specified) | Incomplete on its own — needs a delivery path, which is exactly what C provides. |
| **C. Agent heartbeat command queue** | **Selected.** Matches the existing pull-only transport exactly; no new listening surface on the agent. |
| **D. Short-lived probe lease** | **Selected, combined with C.** A probe result is only trusted for a short TTL after it's reported (proposed: single-digit seconds to a couple minutes, tunable) — after that, it's treated as stale and `verification` falls back to `"central_suggestion"` again, same honesty discipline as `host_stale_after_seconds` already applies to host freshness. |

### Race condition — stated plainly, not hand-waved

```
t0: Central asks lenovoserver to probe port 8000 -- free
t1: probe result reported to Central: "8000 was free at t0"
t2: some OTHER process on lenovoserver binds port 8000
t3: Central allocates port 8000 based on the t1 result
```

This gap is **structurally identical** to the gap that already exists for
the LOCAL case today (`docs/phase8a_agent_allocation.md` already says a
suggestion is never a guarantee; the requesting agent must still run its
own real bind probe before actually using the port). A remote probe adds
a genuine, real data point (an actual `bind()` attempt happened, not just
"no reservation on file") — it narrows the window, it does not close it.
v1.1's copy in both the API response and `docs/` must say this explicitly,
the same way `bind_probe: "not_remote_capable"` already does today —
propose reusing the same field, now with three honest values:
`"not_remote_capable"` (no probe attempted/available), `"remote_probe_stale"`
(a result exists but is past its TTL), `"remote_probe_fresh"` (a result
exists within its TTL — still a point-in-time observation, not a lock,
and documented as such every time it's shown).

### Authentication / result integrity

No new signing scheme needed. A probe result only has meaning if it's
attributable to a specific, genuinely-that-host agent — which is exactly
what the *existing* per-host bearer token on the heartbeat channel
(`require_agent`) already provides. Piggy-backing probe request/response
on the already-authenticated heartbeat body is simpler and no less secure
than inventing a separate signed-probe-result format.

### Abuse consideration

A probe queue is a way to make a remote agent attempt binds on arbitrary
ports at Central's request — bounded blast radius (a `bind()`+immediate
`close()`, never an actual listening service, identical to what
`portforge check` already does locally and safely), but still worth
rate-limiting: cap pending-probes-per-host and total probes per heartbeat
cycle, so a misbehaving or malicious caller can't turn this into a
remote-triggered port-scanning primitive against a host that happens to
run a PortForge agent. See `docs/v1.1/security-review.md`.

## What Central-side plumbing this actually needs (new, not reused)

Unlike every other Phase 8 feature, this is the one v1.1 candidate that
cannot be built purely client-side:

- A new table (or a reuse of `port_observations` with a `probe_requested_at`/
  `probe_result_at` pair — needs a schema decision at implementation time,
  not resolved here) to hold pending/answered probe state.
- The heartbeat response body needs a new, additive field carrying pending
  probes for that host (backward compatible: an old agent that doesn't
  look for the field simply never answers any probes, which is a safe,
  silent degrade — not a hard requirement bump).
- The heartbeat request body needs a new, additive, optional field
  carrying any probe results the agent is reporting.

This is real new backend surface, not just a new CLI command — flagged in
the architecture audit (§6) as the one v1.1 area where "just add a
subcommand" isn't sufficient, and sized accordingly in the roadmap
(v1.1-B is scoped as its own increment specifically because of this).

## Offline/stale host interaction

Already-established policy (`_ensure_host_allocatable`, Phase 8A) refuses
allocation against a STALE or OFFLINE host outright. A probe request
queued for a host that goes offline before its next heartbeat simply never
gets answered — it expires past its own request TTL and reports as
`"not_remote_capable"` again, consistent with existing offline-host
handling rather than inventing a new failure mode.
