"""Token generation and hashing.

Design (see docs/architecture.md "Authentication / enrollment" for the
full write-up): a practical private-deployment scheme, not OAuth.

- Raw tokens are generated with `secrets.token_urlsafe` (cryptographically
  random, URL-safe).
- Only a SHA-256 hash of a token is ever persisted. The raw value exists
  only transiently -- printed once to the admin's terminal when an
  enrollment token is minted, and once to the agent when it establishes
  its host credential -- never written to a database column, a log line,
  or a source file.
- Comparison uses `hmac.compare_digest` (constant-time) to avoid a timing
  side-channel on token verification.

This is deliberately NOT a password-hashing scheme (no bcrypt/scrypt/Argon2,
no per-token salt): these are high-entropy, randomly-generated bearer
tokens, not human-chosen passwords, so a fast, deterministic SHA-256 over
the raw token is the correct tool -- deterministic hashing is in fact
required here so the server can look a presented token up by its hash
directly (an O(1) indexed lookup) rather than hashing every stored
credential and comparing, which a salted password hash would force.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32  # 256 bits of entropy


def generate_token() -> str:
    """A new, cryptographically random bearer token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def tokens_match(raw_token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(raw_token), stored_hash)
