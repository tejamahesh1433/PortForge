"""Central project/service representation.

Deliberately conservative identity strategy (per the project brief: "Do
not over-engineer distributed project identity yet"): a "project" here is
simply a distinct `project_name` string seen across current observations,
aggregated with which hosts/ports/services currently report it. Two hosts
using the same project name are treated as the same project for display
purposes -- there is no separate Project table, UUID, or cross-host
identity resolution. That's an intentional, documented simplification, not
an oversight; see docs/architecture.md "Projects and services".
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from .common import ApiModel


class ProjectServiceEntry(ApiModel):
    host_id: uuid.UUID
    hostname: str
    port: int
    protocol: str
    service_name: Optional[str]
    purpose: Optional[str]
    category: Optional[str]
    state: str


class ProjectOut(ApiModel):
    project_name: str
    host_count: int
    port_count: int
    hosts: List[str]  # hostnames, for a quick human-readable summary
    entries: List[ProjectServiceEntry]
