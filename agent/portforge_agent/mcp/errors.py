from __future__ import annotations

from typing import Any, Optional

from ..config_files import ConfigPathError
from ..config_manager import ConfigError
from ..manifest import ManifestError
from ..targets.models import TargetsError
from ..workflow import WorkflowError


class McpToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[list] = None,
        recovery: Optional[dict] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
        self.recovery = recovery or {}


def error_payload(exc: BaseException) -> dict:
    mapped = map_exception(exc)
    payload: dict[str, Any] = {
        "error": {
            "code": mapped.code,
            "message": mapped.message,
            "details": mapped.details,
        }
    }
    if mapped.recovery:
        payload["error"]["recovery"] = mapped.recovery
    return payload


def map_exception(exc: BaseException) -> McpToolError:
    if isinstance(exc, McpToolError):
        return exc

    if isinstance(exc, TargetsError):
        return McpToolError(exc.code, exc.message, exc.details)

    if isinstance(exc, WorkflowError):
        return McpToolError(exc.code, exc.message, exc.details, exc.recovery)

    if isinstance(exc, ConfigError):
        return McpToolError(exc.code, exc.message, exc.details)

    if isinstance(exc, ManifestError):
        code = exc.code
        if code in ("MANIFEST_INVALID", "MANIFEST_PARSE_ERROR", "MANIFEST_PARSE"):
            code = "INVALID_PROJECT_MANIFEST"
        return McpToolError(code, exc.message, exc.details)

    if isinstance(exc, ConfigPathError):
        return McpToolError(
            "PATH_OUTSIDE_PROJECT",
            str(exc),
            details=[{"code": "CONFIG_PATH_OUTSIDE_PROJECT"}],
        )

    return McpToolError("INTERNAL_ERROR", str(exc))
