#!/usr/bin/env python3
"""Repository-root monorepo bootstrap for clean PortForge development.

Run from the repository root (the directory that contains ``agent/`` and
``backend/``):

    python scripts/bootstrap_dev.py

This installs, into the *active* Python environment:

1. The local ``./agent`` distribution (``portforge-agent``) — non-editable by
   default so clean release validation does not bind to the checkout path.
2. Backend third-party + test dependencies from
   ``backend/requirements-dev.txt`` (no sibling ``../agent`` path).

Optional:

    python scripts/bootstrap_dev.py --editable

installs the agent with ``pip install -e ./agent`` for day-to-day agent work.

This script never uses absolute machine paths, never sets PYTHONPATH, and
never reaches outside the PortForge checkout.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--editable",
        action="store_true",
        help="Install ./agent editable (-e) instead of a normal copy into site-packages",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    agent_dir = root / "agent"
    backend_req = root / "backend" / "requirements-dev.txt"

    if not agent_dir.is_dir():
        print(f"error: expected agent package at {agent_dir}", file=sys.stderr)
        return 2
    if not backend_req.is_file():
        print(f"error: expected {backend_req}", file=sys.stderr)
        return 2

    if Path.cwd().resolve() != root:
        print(
            f"note: bootstrap resolving packages from repository root {root} "
            f"(cwd is {Path.cwd()})",
            file=sys.stderr,
        )

    agent_cmd = [sys.executable, "-m", "pip", "install"]
    if args.editable:
        agent_cmd.extend(["-e", str(agent_dir)])
    else:
        agent_cmd.append(str(agent_dir))

    backend_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(backend_req),
    ]

    print("+", " ".join(agent_cmd), flush=True)
    subprocess.check_call(agent_cmd)
    print("+", " ".join(backend_cmd), flush=True)
    subprocess.check_call(backend_cmd)
    print("bootstrap_dev: OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
