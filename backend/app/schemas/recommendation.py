"""Central recommendation ("suggestion") schema.

This is the single most important semantic line in Phase 5, per the
project brief: the central server can only ever offer a **suggestion**
based on cached/reported observations -- it never performed a fresh
discovery scan, never checked a local reservation file, and never ran a
real socket bind probe on the target machine. Only the agent running on
that specific host can do those three things (see agent/README.md "Three-
layer validation & recommendation algorithm").

`CentralRecommendationOut.verification` is therefore always literally the
string `"central_suggestion"` in Phase 5 -- there is no code path that can
produce `"locally_verified"` yet, because that would require the central
server to call out to a specific online agent and get a live answer back,
which is explicitly future work (Phase 6). The field exists now, with this
one fixed value, specifically so Phase 6 can add the live path without an
API-shape change -- clients should already be branching on this field
rather than assuming every recommendation is equally trustworthy.
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
    basis: str  # short human-readable explanation of what this suggestion IS and ISN'T
    candidates_considered: int
    known_conflicts_excluded: List[int] = []
