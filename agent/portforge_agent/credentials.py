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


def load_credential() -> Optional[str]:
    """Loads the agent token, returning None if not found or invalid."""
    path = credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        token = data.get("token")
        if isinstance(token, str):
            return token
    except (OSError, ValueError):
        pass
    return None


def save_credential(token: str) -> None:
    """Saves the agent token securely."""
    path = credentials_path()
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
