from __future__ import annotations

import json
import sys
from typing import Any, Optional, TextIO


def decode_message(line: str) -> dict:
    return json.loads(line)


def encode_message(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))


def read_message(stdin: TextIO) -> Optional[dict]:
    line = stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return read_message(stdin)
    return decode_message(line)


def write_message(stdout: TextIO, msg: dict) -> None:
    stdout.write(encode_message(msg) + "\n")
    stdout.flush()
