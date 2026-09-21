# v1.1 Backlog: Repository Housekeeping (deferred, not part of v1.1-A)

Per `docs/v1.1/architecture-audit.md` §1 and this increment's explicit
instruction not to touch it: the repository root has real, git-tracked
scratch/debugging artifacts left over from ad hoc physical validation,
none of which are referenced by any build, test, or CLI entry point:

- `diag.json`, `diag_lenovo.json`, `dummy_pid.txt`, `health.json`,
  `hosts.json`, `insert_host.py`, `lenovo_ports.json`, `simulate_agent.py`,
  `simulate_agent_cleanup.py`, `simulate_recovery.py`, `status.txt`,
  `test_robustness.py`, `write_remote_script.py`, `write_script.py`
- Stub directories holding only a placeholder `README.md`: `cli/`,
  `frontend/`, `scripts/` (note: v1.1-E's proposed install scripts would
  give `scripts/` its first real content, at which point this entry
  should be re-evaluated rather than blindly cleaned), `tests/`
- `robustness/portforge.yml` — a stray physical-test manifest

**Not removed in v1.1-A** (explicit instruction: housekeeping must not
contaminate this increment's functional diff). Proposed as a small,
standalone housekeeping commit/PR, reviewed on its own, ideally before or
alongside v1.1-E (which will add the first real content to `scripts/`
anyway).

Also noted, not acted on: `venv_agent/` (~244 MB) is present in the
working tree but not git-tracked and not explicitly `.gitignore`d — low
risk as-is, but adding an explicit ignore entry would prevent it from
ever being accidentally committed later.
