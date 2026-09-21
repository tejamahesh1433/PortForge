"""Phase 8C: safe, minimal-diff dotenv file editing.

No dotenv parser/writer existed anywhere in this codebase before Phase 8C
(see docs/phase8c_config_audit.md §5) -- this is a small, hand-rolled,
line-based editor, not a full dotenv grammar implementation. It only ever
recognizes and rewrites lines matching one of the MAPPED keys; every other
line (comments, blank lines, unrelated assignments, anything it doesn't
recognize) passes through completely unchanged, byte-for-byte in spirit
(newline style and trailing-newline presence are also preserved when no
key is being appended -- see `compute_dotenv_update`).

Deliberately does not execute, source, or interpolate anything in the
file -- it is pure text in, pure text out.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_ASSIGNMENT_PREFIX_RE = r"^(?P<prefix>\s*(?:export\s+)?)"


class DotenvError(Exception):
    def __init__(self, code: str, message: str, details: Optional[List[dict]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class DotenvChange:
    key: str
    before: Optional[str]  # None if the key was appended (didn't exist before)
    after: str
    action: str  # "update" | "append"


def _key_regex(key: str) -> "re.Pattern":
    return re.compile(_ASSIGNMENT_PREFIX_RE + re.escape(key) + r"=(?P<rest>.*)$")


def _split_value_and_comment(rest: str) -> Tuple[str, str]:
    """Splits a dotenv value from a trailing `# comment`, respecting
    quotes so a `#` inside a quoted value isn't mistaken for a comment.
    """
    in_single = in_double = False
    for i, ch in enumerate(rest):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return rest[:i], rest[i:]
    return rest, ""


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def compute_dotenv_update(original_text: Optional[str], mapping: Dict[str, str]) -> Tuple[str, List[DotenvChange]]:
    """`mapping`: {ENV_KEY: new_value}. `original_text` is None for a file
    that doesn't exist yet (Phase 8C may create one -- see manifest.py
    "DotenvFileMapping" / config_manager.py "will_create"). Returns
    `(new_text, changes)`. Raises `DotenvError("DOTENV_DUPLICATE_KEY", ...)`
    -- checked for every mapped key before any line is rewritten -- if any
    mapped key appears as an assignment more than once in the file.
    """
    if original_text is None or original_text == "":
        lines: List[str] = []
        newline = "\n"
        had_trailing_newline = True
    else:
        newline = _detect_newline(original_text)
        ends_with_nl = original_text.endswith(newline)
        body = original_text[: -len(newline)] if ends_with_nl else original_text
        lines = body.split(newline)
        had_trailing_newline = ends_with_nl

    # Find every occurrence of every mapped key FIRST, and fail on any
    # duplicate BEFORE rewriting anything -- no partial mutation on a
    # duplicate-key error.
    occurrences: Dict[str, List[int]] = {key: [] for key in mapping}
    for key in mapping:
        pattern = _key_regex(key)
        for i, line in enumerate(lines):
            if pattern.match(line):
                occurrences[key].append(i)

    duplicates = {key: idxs for key, idxs in occurrences.items() if len(idxs) > 1}
    if duplicates:
        raise DotenvError(
            "DOTENV_DUPLICATE_KEY",
            "The following key(s) appear more than once in the dotenv file, so PortForge cannot safely "
            f"determine which occurrence controls behavior: {', '.join(sorted(duplicates))}.",
            details=[{"key": key, "line_numbers": [i + 1 for i in idxs]} for key, idxs in duplicates.items()],
        )

    changes: List[DotenvChange] = []
    new_lines = list(lines)
    appended_any = False

    for key, new_value in mapping.items():
        idxs = occurrences[key]
        if idxs:
            i = idxs[0]
            match = _key_regex(key).match(lines[i])
            rest = match.group("rest")
            prefix = match.group("prefix")
            _value_part, comment_part = _split_value_and_comment(rest)
            before = _value_part.strip()
            suffix = f"  {comment_part}" if comment_part else ""
            new_lines[i] = f"{prefix}{key}={new_value}{suffix}"
            if before != new_value:
                changes.append(DotenvChange(key=key, before=before, after=new_value, action="update"))
        else:
            new_lines.append(f"{key}={new_value}")
            changes.append(DotenvChange(key=key, before=None, after=new_value, action="append"))
            appended_any = True

    trailing = True if appended_any else had_trailing_newline
    new_text = newline.join(new_lines) + (newline if trailing else "")
    return new_text, changes
