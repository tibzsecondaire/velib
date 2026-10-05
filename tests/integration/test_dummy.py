"""Smoke test to validate the integration test pipeline."""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_smoke() -> None:
    assert 1 + 1 == 2
