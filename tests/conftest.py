"""Shared test fixtures for z3rno-server."""

from __future__ import annotations

import pytest

# Dev API key used across all tests to bypass DB auth verification
DEV_API_KEY = "z3rno_sk_test_localdev"
DEV_ORG_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _set_dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set dev API key environment variables for all tests.

    This bypasses BCrypt + DB verification in AuthMiddleware,
    allowing tests to run without a real database or Valkey.
    """
    monkeypatch.setenv("Z3RNO_API_KEY", DEV_API_KEY)
    monkeypatch.setenv("Z3RNO_DEV_ORG_ID", DEV_ORG_ID)
