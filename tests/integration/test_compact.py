"""Tests for the daily compaction, on a real temporary git repository."""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from velib import gbfs
from velib.collect import cli
from velib.collect.compact import CompactionError, compact_day
from velib.collect.snapshot import write_snapshot
from velib.gbfs import StationInfo, StationStatus, StatusSnapshot

pytestmark = pytest.mark.integration

DAY = date(2026, 10, 5)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def _snapshot(mechanical: int) -> StatusSnapshot:
    stations = [
        StationStatus(
            station_id=station_id,
            mechanical=mechanical,
            ebike=1,
            docks=10,
            is_installed=True,
            is_renting=True,
            is_returning=True,
            last_reported=1791200000,
        )
        for station_id in (2, 1)
    ]
    updated = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    return StatusSnapshot(feed_updated_at=updated, stations=stations)


def _record(repo: Path, fetched_at: datetime, mechanical: int) -> None:
    message = write_snapshot(_snapshot(mechanical), repo / "raw", fetched_at)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "velib-data"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("velib-data\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "build: start the repository")
    _record(path, datetime(2026, 10, 4, 23, 58, tzinfo=UTC), mechanical=1)
    _record(path, datetime(2026, 10, 5, 0, 3, tzinfo=UTC), mechanical=2)
    _record(path, datetime(2026, 10, 5, 0, 13, tzinfo=UTC), mechanical=3)
    _git(path, "commit", "-q", "--allow-empty", "-m", "compact 2026-10-04")
    return path


def test_compact_day_keeps_only_the_snapshots_of_that_utc_day(repo: Path) -> None:
    summary = compact_day(repo, DAY)
    frame = pl.read_parquet(repo / "daily" / "2026-10-05.parquet")
    assert summary.snapshots == 2
    assert frame.height == 4
    assert frame.get_column("station_id").to_list() == [1, 1, 2, 2]
    assert frame.get_column("mechanical").to_list() == [2, 3, 2, 3]


def test_compact_day_writes_typed_columns(repo: Path) -> None:
    compact_day(repo, DAY)
    frame = pl.read_parquet(repo / "daily" / "2026-10-05.parquet")
    assert frame.schema == pl.Schema(
        {
            "fetched_at": pl.Datetime("us", "UTC"),
            "feed_updated_at": pl.Datetime("us", "UTC"),
            "station_id": pl.Int64(),
            "mechanical": pl.Int16(),
            "ebike": pl.Int16(),
            "docks": pl.Int16(),
            "is_installed": pl.Boolean(),
            "is_renting": pl.Boolean(),
            "is_returning": pl.Boolean(),
            "last_reported": pl.Datetime("us", "UTC"),
        }
    )
    assert frame.get_column("fetched_at").min() == datetime(2026, 10, 5, 0, 3, tzinfo=UTC)
    assert frame.get_column("last_reported").max() == datetime.fromtimestamp(1791200000, tz=UTC)


def test_compact_day_writes_the_index_and_replaces_its_line_on_rerun(repo: Path) -> None:
    compact_day(repo, DAY)
    compact_day(repo, DAY)
    lines = (repo / "daily" / "index.csv").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "date,snapshots,first_fetch,last_fetch,max_gap_minutes,stations,rows",
        "2026-10-05,2,2026-10-05T00:03:00Z,2026-10-05T00:13:00Z,10.0,2,4",
    ]


def test_compact_day_without_snapshot_fails(repo: Path) -> None:
    with pytest.raises(CompactionError, match="2026-10-07"):
        compact_day(repo, date(2026, 10, 7))


def test_compact_command_also_writes_station_information(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stations = [
        StationInfo(
            station_id=2,
            station_code="16107",
            name="Benjamin Godard - Victor Hugo",
            lat=48.865983,
            lon=2.275725,
            capacity=35,
        ),
        StationInfo(
            station_id=1,
            station_code="00001",
            name="Test, with comma",
            lat=48.85,
            lon=2.35,
            capacity=20,
        ),
    ]

    def fake_information(client: httpx.Client) -> list[StationInfo]:
        return stations

    monkeypatch.setattr(gbfs, "fetch_station_information", fake_information)
    exit_code = cli.main(["compact", "--day", "2026-10-05", "--repo", str(repo)])
    assert exit_code == 0
    assert (repo / "daily" / "2026-10-05.parquet").is_file()
    lines = (repo / "stations" / "station_information.csv").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "station_id,station_code,name,lat,lon,capacity",
        '1,00001,"Test, with comma",48.85,2.35,20',
        "2,16107,Benjamin Godard - Victor Hugo,48.865983,2.275725,35",
    ]


def test_compact_command_reports_a_day_without_snapshot(repo: Path) -> None:
    assert cli.main(["compact", "--day", "2026-10-07", "--repo", str(repo)]) == 1
