from __future__ import annotations

DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        "coverage",
        "target",
        "vendor",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".portforge",
    }
)


def is_ignored_dir(name: str) -> bool:
    return name in DEFAULT_IGNORE_DIRS
