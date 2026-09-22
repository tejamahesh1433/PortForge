"""v1.1-E: credentials.py -- the daemon's own credential store, and the
dual-credential-store fix (central_sync.py::enroll now writes here too,
plus a read-side fallback for hosts enrolled before this fix shipped).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from portforge_agent import central_sync
from portforge_agent.central_config import CentralConfig
from portforge_agent.credentials import load_credential, save_credential


def test_round_trip_with_explicit_path(tmp_path):
    path = tmp_path / "credentials.json"
    save_credential("my-token", path=path)
    assert load_credential(path=path) == "my-token"


def test_missing_file_returns_none(tmp_path):
    assert load_credential(path=tmp_path / "does-not-exist.json") is None


def test_malformed_json_returns_none_not_raises(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("not valid json{{{")
    assert load_credential(path=path) is None


def test_saved_file_has_restrictive_permissions_on_unix(tmp_path):
    import stat
    import sys

    if sys.platform == "win32":
        return  # Windows permission model is asserted separately elsewhere
    path = tmp_path / "credentials.json"
    save_credential("secret-token", path=path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == (stat.S_IRUSR | stat.S_IWUSR)


# --- dual-credential-store fix (task v1.1-E Sec13) --------------------------


def test_central_enroll_now_also_writes_credentials_json(tmp_path):
    """The real bug found during v1.1-D physical acceptance: `central
    enroll` used to write ONLY central.json's embedded token, never
    credentials.json -- the ONLY store the always-on daemon reads. This
    proves the fix: after `central_sync.enroll()`, the sibling
    credentials.json in the same directory has the same token.
    """
    client = MagicMock()
    client.enroll.return_value = MagicMock(success=True, data={"agent_token": "the-real-token"})

    central_json = tmp_path / "central.json"
    with patch("portforge_agent.central_sync.CentralClient", return_value=client):
        result = central_sync.enroll(central_json, "http://central.example", "enroll-tok")

    assert result.success
    credentials_json = tmp_path / "credentials.json"
    assert credentials_json.exists()
    assert load_credential(path=credentials_json) == "the-real-token"


def test_central_enroll_still_writes_central_json_token_unchanged(tmp_path):
    """Backward compatible: central.json's own embedded token (read by the
    older `central sync`/`central status` one-off commands) is unchanged.
    """
    from portforge_agent.central_config import load_central_config

    client = MagicMock()
    client.enroll.return_value = MagicMock(success=True, data={"agent_token": "the-real-token"})

    central_json = tmp_path / "central.json"
    with patch("portforge_agent.central_sync.CentralClient", return_value=client):
        central_sync.enroll(central_json, "http://central.example", "enroll-tok")

    assert load_central_config(path=central_json).token == "the-real-token"


def test_load_credential_falls_back_to_central_json_for_legacy_enrollment():
    """A host enrolled via the OLD central-enroll-only behavior (before
    this fix) has a valid token sitting only in central.json. This must
    keep working without forcing re-enrollment -- task Sec14 "enrollment
    preservation". Uses the REAL default paths (no path= override) since
    the fallback is deliberately real-path-only -- see load_credential's
    own docstring for why an isolated test path never falls back to a
    different file.
    """
    with patch("portforge_agent.credentials.credentials_path") as mock_cred_path, \
         patch("portforge_agent.central_config.load_central_config") as mock_load_central:
        mock_cred_path.return_value = MagicMock(exists=MagicMock(return_value=False))
        mock_load_central.return_value = CentralConfig(enabled=True, url="http://x", token="legacy-token")

        with patch("portforge_agent.credentials.save_credential") as mock_save:
            token = load_credential()

    assert token == "legacy-token"
    mock_save.assert_called_once_with("legacy-token")  # self-heals into credentials.json


def test_load_credential_prefers_credentials_json_over_legacy_fallback(tmp_path):
    """If credentials.json already has a token, the central.json fallback
    is never consulted -- credentials.json is authoritative once present.
    """
    path = tmp_path / "credentials.json"
    save_credential("current-token", path=path)

    with patch("portforge_agent.central_config.load_central_config") as mock_load_central:
        token = load_credential(path=path)

    assert token == "current-token"
    mock_load_central.assert_not_called()


def test_load_credential_returns_none_when_neither_store_has_a_token():
    with patch("portforge_agent.credentials.credentials_path") as mock_cred_path, \
         patch("portforge_agent.central_config.load_central_config") as mock_load_central:
        mock_cred_path.return_value = MagicMock(exists=MagicMock(return_value=False))
        mock_load_central.return_value = CentralConfig()  # never enrolled at all

        assert load_credential() is None


def test_isolated_test_path_never_falls_back_to_the_real_central_json(tmp_path):
    """An explicit path= override means pure isolation for a test -- it
    must never silently consult the REAL machine's central.json.
    """
    with patch("portforge_agent.central_config.load_central_config") as mock_load_central:
        result = load_credential(path=tmp_path / "isolated-credentials.json")

    assert result is None
    mock_load_central.assert_not_called()
