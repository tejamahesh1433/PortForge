from __future__ import annotations

from .errors import McpToolError


def require_mutate_approval(arguments: dict) -> None:
    if arguments.get("confirm_mutate") is not True:
        raise McpToolError(
            "MUTATION_NOT_APPROVED",
            "Mutating tools require confirm_mutate: true.",
            details=[{"field": "confirm_mutate", "expected": True}],
        )
