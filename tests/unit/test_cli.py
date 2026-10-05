"""Tests for the velib-collect command line."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from velib import gbfs
from velib.collect import cli
from velib.gbfs import StationStatus, StatusSnapshot

SNAPSHOT = StatusSnapshot(
    feed_updated_at=datetime(2026, 10, 5, 12, 6, 55, tzinfo=UTC),
    stations=[
        StationStatus(
            station_id=10,
            mechanical=4,
            ebike=5,
            docks=6,
            is_installed=True,
            is_renting=True,
            is_returning=False,
            last_reported=1791202010,
        )
    ],
)


def test_snapshot_command_writes_files_and_prints_the_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gbfs, "fetch_station_status", lambda client: SNAPSHOT)
    exit_code = cli.main(["snapshot", "--output-dir", str(tmp_path / "raw")])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("snapshot fetched_at=")
    assert out.endswith(" feed_updated_at=2026-10-05T12:06:55Z\n")
    assert (tmp_path / "raw" / "station_status.csv").is_file()
    assert (tmp_path / "raw" / "snapshot_meta.json").is_file()


def test_snapshot_command_fails_without_writing_on_a_feed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(client: httpx.Client) -> StatusSnapshot:
        raise gbfs.ForbiddenError("403")

    monkeypatch.setattr(gbfs, "fetch_station_status", forbidden)
    exit_code = cli.main(["snapshot", "--output-dir", str(tmp_path / "raw")])
    assert exit_code == 1
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "raw").exists()
