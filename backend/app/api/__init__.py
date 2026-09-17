"""API routers.

Two conceptually separate groups, mirroring the project brief's
"authenticated agent endpoints should be separated conceptually from
human/read APIs":

- `agents.py`: authenticated-agent-only (`require_agent`/`require_admin`)
  write endpoints -- enrollment, heartbeat, observation ingestion,
  agent-driven reservation sync. Mounted under `/api/agent/*`.
- `hosts.py`, `ports.py`, `projects.py`, `reservations.py`,
  `recommendations.py`, `conflicts.py`, `health.py`: read-oriented human/
  dashboard-facing APIs under `/api/*`. `reservations.py` also exposes an
  authenticated POST/DELETE for agent-driven central reservation sync,
  kept in this file (rather than agents.py) because it shares response
  shape and query filters with the read side.

Every route function is thin: parse/validate via a Pydantic schema
(FastAPI does this automatically), call exactly one service function, map
the result to a response schema. No route embeds a database query or a
business rule inline -- see repositories/__init__.py and
services/__init__.py.
"""
