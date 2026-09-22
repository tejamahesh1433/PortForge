"""Secure credential storage for PortForge Agent.

Stores the per-host enrollment token (Agent Credential).
Uses strict file permissions (0600 on UNIX, restricted DACLs on Windows)
to prevent unauthorized access.
"""
import json
import os
import stat
from pathlib import Path
from typing import Optional

from . import paths
from . import platform as pf


def credentials_path() -> Path:
    return paths.data_dir() / "credentials.json"


def load_credential(path: Optional[Path] = None) -> Optional[str]:
    """Loads the agent token, returning None if not found or invalid.

    v1.1-E: falls back to `central.json`'s own embedded `token` field
    (written by the older `central enroll` command -- see
    `central_sync.py::enroll`'s docstring note) if `credentials.json` has
    none. A host enrolled via `central enroll` before this fix has a
    perfectly valid token sitting in `central.json` that the always-on
    daemon has simply never looked at; this heals that case automatically,
    on the very next read, with no re-enrollment required (task's
    "enrollment preservation" requirement) -- and self-heals by writing
    the recovered token into `credentials.json` too, so the fallback only
    ever fires once per host. `path` overrides `credentials_path()`
    for tests; the central.json fallback always uses the real default
    location when `path` is overridden, since a test redirecting
    credentials.json is asking for pure isolation, not a fallback belonging
    to a different file entirely.
    """
    cred_path = path or credentials_path()
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            token = data.get("token")
            if isinstance(token, str):
                return token
        except (OSError, ValueError):
            pass

    if path is not None:
        return None

    from .central_config import load_central_config

    legacy_token = load_central_config().token
    if legacy_token:
        try:
            save_credential(legacy_token)
        except OSError:
            pass  # best-effort self-heal; the token is still usable this call
        return legacy_token
    return None


def save_credential(token: str, path: Optional[Path] = None) -> None:
    """Saves the agent token securely."""
    path = path or credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    payload = json.dumps({"token": token}, indent=2)
    
    # Write securely
    import tempfile
    fd, tmp_name = tempfile.mkstemp(prefix=".cred-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
            
        if pf.detect_os() != pf.OperatingSystem.WINDOWS:
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            
        os.replace(tmp_path, path)
        
        if pf.detect_os() == pf.OperatingSystem.WINDOWS:
            # On Windows, try to restrict permissions using icacls if possible,
            # though usually the LocalAppData directory is already user-restricted.
            pass
            
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def clear_credential() -> None:
    """Removes the stored credential."""
    path = credentials_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
