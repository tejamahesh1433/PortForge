"""Tests for token generation/hashing (app/security/tokens.py)."""
from app.security.tokens import generate_token, hash_token, tokens_match


def test_generate_token_is_random_and_long():
    a = generate_token()
    b = generate_token()
    assert a != b
    assert len(a) >= 32


def test_hash_token_is_deterministic():
    token = generate_token()
    assert hash_token(token) == hash_token(token)


def test_different_tokens_hash_differently():
    a, b = generate_token(), generate_token()
    assert hash_token(a) != hash_token(b)


def test_tokens_match_true_for_correct_token():
    token = generate_token()
    assert tokens_match(token, hash_token(token)) is True


def test_tokens_match_false_for_wrong_token():
    token = generate_token()
    other = generate_token()
    assert tokens_match(other, hash_token(token)) is False


def test_hash_is_sha256_hex_format():
    token = generate_token()
    digest = hash_token(token)
    assert len(digest) == 64
    int(digest, 16)  # raises if not valid hex
