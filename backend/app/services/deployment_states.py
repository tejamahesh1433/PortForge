"""Deployment attempt state vocabulary (Phase 18).

Non-terminal states hold the partial-unique concurrency lock on
(project, environment, host_id). Terminal states release it.
"""

TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "ROLLED_BACK"})
NON_TERMINAL_STATES = frozenset(
    {
        "APPROVED",
        "PREPARING",
        "TRANSFERRING",
        "STARTING",
        "VERIFYING",
        "ROLLING_BACK",
    }
)
