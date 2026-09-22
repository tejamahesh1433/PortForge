"""Central recommendation ("suggestion") schema.

This is the single most important semantic line in Phase 5, per the
project brief: the central server itself can only ever offer a
**suggestion** based on cached/reported observations -- Central never
performs a fresh discovery scan, never checks a local reservation file,
and never runs a real socket bind probe on the target machine itself.
Only the agent running on that specific host can do those three things
(see agent/README.md "Three-layer validation & recommendation algorithm").

`CentralRecommendationOut.verification` was always literally the string
`"central_suggestion"` through v1.1-A -- there was no code path that could
produce `"locally_verified"`, because that requires the central server to
ask a specific REMOTE agent to perform a real check and report back, which
is exactly what v1.1-B's remote bind-probe adds (see
docs/v1.1/remote-probe-design.md). The field's contract is unchanged by
this: it still always means "the TARGET HOST'S OWN AGENT confirmed this,
not Central" -- Central still never probes anything itself.

`bind_probe` (v1.1-B, additive) carries the finer-grained classification
behind `verification`: `"not_remote_capable"` (unchanged v1.0 default --
no fresh probe evidence exists), `"verified_free"`/`"verified_occupied"`
(a fresh, real probe from the target agent), `"expired"` (a probe existed
but is past its TTL -- not trusted), `"unavailable"` (the agent's own
probe ATTEMPT failed, e.g. a permission error). See
services/probe_service.py for the exact classification logic -- this is
still a point-in-time observation, never a guarantee (the
probe-then-race gap is inherent and explicitly documented, not solved).
"""
from __future__ import annotations

from typing import List, Literal, Optional

from .common import ApiModel

VerificationLevel = Literal["central_suggestion", "locally_verified"]


class CentralRecommendationOut(ApiModel):
    service_type: str
    protocol: str
    recommended_port: Optional[int]
    verification: VerificationLevel
    bind_probe: str = "not_remote_capable"
    basis: str  # short human-readable explanation of what this suggestion IS and ISN'T
    candidates_considered: int
    known_conflicts_excluded: List[int] = []
