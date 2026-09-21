"""Phase 8C §32-34: dotenv editor tests."""
from __future__ import annotations

import pytest

from portforge_agent.dotenv_editor import DotenvChange, DotenvError, compute_dotenv_update


def test_update_preserves_unrelated_keys_and_comments():
    original = "API_PORT=9000\nSECRET=value\n# comment\n"
    new_text, changes = compute_dotenv_update(original, {"API_PORT": "8000"})
    assert new_text == "API_PORT=8000\nSECRET=value\n# comment\n"
    assert changes == [DotenvChange(key="API_PORT", before="9000", after="8000", action="update")]


def test_append_missing_key_cleanly():
    original = "EXISTING=1\n"
    new_text, changes = compute_dotenv_update(original, {"NEW_KEY": "3000"})
    assert new_text == "EXISTING=1\nNEW_KEY=3000\n"
    assert len(changes) == 1
    assert changes[0].action == "append"
    assert changes[0].before is None
    assert changes[0].after == "3000"


def test_append_to_nonexistent_file_creates_content():
    new_text, changes = compute_dotenv_update(None, {"API_PORT": "8000"})
    assert new_text == "API_PORT=8000\n"
    assert changes[0].action == "append"


def test_duplicate_key_fails_safely_zero_changes():
    original = "API_PORT=1\nAPI_PORT=2\nOTHER=x\n"
    with pytest.raises(DotenvError) as exc_info:
        compute_dotenv_update(original, {"API_PORT": "8000"})
    assert exc_info.value.code == "DOTENV_DUPLICATE_KEY"


def test_duplicate_key_checked_before_any_other_key_is_mutated():
    """Even if only ONE of several mapped keys is duplicated, nothing
    should be computed as changed -- fail before touching anything.
    """
    original = "A=1\nA=2\nB=1\n"
    with pytest.raises(DotenvError):
        compute_dotenv_update(original, {"A": "9", "B": "9"})


def test_crlf_preserved():
    original = "API_PORT=1\r\nX=2\r\n"
    new_text, _ = compute_dotenv_update(original, {"API_PORT": "9"})
    assert new_text == "API_PORT=9\r\nX=2\r\n"
    assert "\n" not in new_text.replace("\r\n", "")


def test_no_trailing_newline_preserved_when_only_updating():
    original = "API_PORT=1"
    new_text, _ = compute_dotenv_update(original, {"API_PORT": "9"})
    assert new_text == "API_PORT=9"


def test_export_prefix_preserved():
    original = "export API_PORT=1\n"
    new_text, changes = compute_dotenv_update(original, {"API_PORT": "9"})
    assert new_text == "export API_PORT=9\n"


def test_inline_comment_preserved():
    original = "API_PORT=1  # the api port\n"
    new_text, changes = compute_dotenv_update(original, {"API_PORT": "9"})
    assert new_text == "API_PORT=9  # the api port\n"
    assert changes[0].before == "1"


def test_no_change_when_value_already_matches():
    original = "API_PORT=8000\n"
    new_text, changes = compute_dotenv_update(original, {"API_PORT": "8000"})
    assert new_text == original
    assert changes == []


def test_multiple_keys_mixed_update_and_append():
    original = "FRONTEND_PORT=3000\n"
    new_text, changes = compute_dotenv_update(original, {"FRONTEND_PORT": "3001", "API_PORT": "8000"})
    assert new_text == "FRONTEND_PORT=3001\nAPI_PORT=8000\n"
    actions = {c.key: c.action for c in changes}
    assert actions == {"FRONTEND_PORT": "update", "API_PORT": "append"}
