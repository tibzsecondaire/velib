"""Builds the documentation site in strict mode."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def test_site_builds_in_strict_mode(tmp_path: Path) -> None:
    pytest.importorskip("mkdocs")
    pytest.importorskip("material")
    site = tmp_path / "site"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (site / "map" / "index.html").is_file()
    assert (site / "javascripts" / "map-core.mjs").is_file()
    assert (site / "javascripts" / "map.mjs").is_file()
    assert not (site / "superpowers").exists()
