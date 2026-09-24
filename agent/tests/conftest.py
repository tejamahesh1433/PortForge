"""Pytest fixtures shared across agent tests."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from .mcp_helpers import CENTRAL_PATCH_TARGET, central_mock


@pytest.fixture()
def mock_central_client():
    client = central_mock()
    with patch(CENTRAL_PATCH_TARGET) as mock_cls:
        mock_cls.return_value = client
        yield client


@pytest.fixture(autouse=True)
def _autouse_mock_central_for_mcp_tests(request):
    """Only MCP test modules get an automatic CentralClient mock."""
    basename = getattr(request.node.fspath, "basename", "") or ""
    if not str(basename).startswith("test_mcp"):
        yield
        return
    client = central_mock()
    with patch(CENTRAL_PATCH_TARGET) as mock_cls:
        mock_cls.return_value = client
        request.node._mcp_central_client = client  # noqa: SLF001 — test access
        yield client
