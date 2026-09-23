"""Unit tests for version comparison utilities (Phase 9)."""
import pytest

from app.services.version_compare import compare, update_availability


class TestCompare:
    def test_equal_versions(self):
        assert compare("1.0.0", "1.0.0") == 0

    def test_less_than(self):
        assert compare("1.0.0", "1.1.0") == -1

    def test_greater_than(self):
        assert compare("2.0.0", "1.9.9") == 1

    def test_minor_version_beats_patch(self):
        assert compare("1.9.0", "1.10.0") == -1

    def test_patch_comparison(self):
        assert compare("1.0.1", "1.0.0") == 1

    def test_unparseable_returns_zero(self):
        assert compare("not-a-version", "1.0.0") == 0

    def test_both_unparseable_returns_zero(self):
        assert compare("bad", "also-bad") == 0

    def test_pre_release_less_than_release(self):
        assert compare("1.0.0a1", "1.0.0") == -1


class TestUpdateAvailability:
    def test_unknown_when_no_agent_version(self):
        assert update_availability(None, "1.1.0") == "UNKNOWN"

    def test_unknown_when_no_target(self):
        assert update_availability("1.0.0", None) == "UNKNOWN"

    def test_unknown_when_both_none(self):
        assert update_availability(None, None) == "UNKNOWN"

    def test_current_when_equal(self):
        assert update_availability("1.1.0", "1.1.0") == "CURRENT"

    def test_update_available_when_older(self):
        assert update_availability("1.0.0", "1.1.0") == "UPDATE_AVAILABLE"

    def test_unsupported_when_newer(self):
        assert update_availability("1.2.0", "1.1.0") == "UNSUPPORTED"

    def test_unknown_for_bad_agent_version(self):
        assert update_availability("not-a-version", "1.0.0") == "UNKNOWN"

    def test_unknown_for_bad_target_version(self):
        assert update_availability("1.0.0", "not-a-version") == "UNKNOWN"

    def test_minor_version_numbers(self):
        assert update_availability("1.9.0", "1.10.0") == "UPDATE_AVAILABLE"
