"""v1.1-A: pure unit tests for the advisory protocol-compatibility
evaluation -- no DB, no HTTP, matching services/compatibility_service.py's
own "pure, stateless function" design.
"""
from app.services.compatibility_service import (
    COMPATIBLE,
    PROTOCOL_VERSION,
    UNKNOWN,
    WARNING,
    evaluate_protocol_compatibility,
)


def test_missing_protocol_version_is_unknown():
    assert evaluate_protocol_compatibility(None) == UNKNOWN


def test_matching_protocol_version_is_compatible():
    assert evaluate_protocol_compatibility(PROTOCOL_VERSION) == COMPATIBLE


def test_older_protocol_version_is_warning():
    assert evaluate_protocol_compatibility(max(PROTOCOL_VERSION - 1, 0)) == WARNING


def test_newer_protocol_version_is_warning():
    assert evaluate_protocol_compatibility(PROTOCOL_VERSION + 1) == WARNING


def test_never_raises_on_any_int_input():
    for value in (-100, -1, 0, 1, PROTOCOL_VERSION, 9999):
        result = evaluate_protocol_compatibility(value)
        assert result in (COMPATIBLE, WARNING, UNKNOWN)
