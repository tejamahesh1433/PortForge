"""Platform-aware user-level data directory for PortForge's local state
(reservations, config, lock file).

Never hardcodes a username or machine path -- resolved dynamically via
platform-appropriate environment variables / home directory, matching each
OS's own convention, so this works unmodified on another computer/user.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from . import platform as pf


def data_dir() -> Path:
    """The directory PortForge stores its local state in.

    Windows:  %LOCALAPPDATA%\\PortForge
    macOS:    ~/Library/Application Support/PortForge
    Linux:    $XDG_DATA_HOME/portforge, else ~/.local/share/portforge
    """
    system = pf.detect_os()

    if system == pf.OperatingSystem.WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "PortForge"
        return Path.home() / "AppData" / "Local" / "PortForge"

    if system == pf.OperatingSystem.MACOS:
        return Path.home() / "Library" / "Application Support" / "PortForge"

    # Linux and any other POSIX-like OS: XDG Base Directory spec.
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "portforge"
    return Path.home() / ".local" / "share" / "portforge"


def reservations_path() -> Path:
    return data_dir() / "reservations.json"


def lock_path() -> Path:
    return data_dir() / "reservations.lock"


def config_path_candidates() -> List[Path]:
    """Ordered candidate locations for the user-level config file. The
    first one found (checked in this order) wins -- see config.py.
    """
    d = data_dir()
    return [d / "config.yml", d / "config.yaml", d / "config.json"]
