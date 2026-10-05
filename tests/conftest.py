"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Returns a temporary data directory isolated per test."""
    data = tmp_path / "data"
    data.mkdir()

    return data
