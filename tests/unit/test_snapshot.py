"""Tests for the raw snapshot format."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from velib.collect.snapshot import (
    SnapshotTimes,
    format_commit_message,
    format_time,
    parse_commit_message,
    write_snapshot,
)
from velib.gbfs import StationStatus, StatusSnapshot

FEED_UPDATED_AT = datetime(2026, 10, 5, 12, 6, 55, tzinfo=UTC)
FETCHED_AT = datetime(2026, 10, 5, 12, 7, 31, 123456, tzinfo=UTC)


def _station(station_id: int, mechanical: int, ebike: int, docks: int) -> StationStatus:
    return StationStatus(
        station_id=station_id,
        mechanical=mechanical,
        ebike=ebike,
        docks=docks,
        is_installed=True,
        is_renting=True,
        is_returning=False,
        last_reported=1791202000 + station_id,
    )


def test_write_snapshot_writes_a_sorted_csv(tmp_path: Path) -> None:
    snapshot = StatusSnapshot(FEED_UPDATED_AT, [_station(20, 1, 2, 3), _station(10, 4, 5, 6)])
    write_snapshot(snapshot, tmp_path / "raw", FETCHED_AT)
    content = (tmp_path / "raw" / "station_status.csv").read_bytes().decode("utf-8")
    assert content == (
        "station_id,mechanical,ebike,docks,is_installed,is_renting,is_returning,last_reported\n"
        "10,4,5,6,1,1,0,1791202010\n"
        "20,1,2,3,1,1,0,1791202020\n"
    )


def test_write_snapshot_writes_meta_and_returns_the_commit_message(tmp_path: Path) -> None:
    snapshot = StatusSnapshot(FEED_UPDATED_AT, [_station(10, 4, 5, 6)])
    message = write_snapshot(snapshot, tmp_path, FETCHED_AT)
    meta = (tmp_path / "snapshot_meta.json").read_text(encoding="utf-8")
    assert meta.endswith("}\n")
    assert json.loads(meta) == {
        "fetched_at": "2026-10-05T12:07:31Z",
        "feed_updated_at": "2026-10-05T12:06:55Z",
        "stations": 1,
    }
    expected = "snapshot fetched_at=2026-10-05T12:07:31Z feed_updated_at=2026-10-05T12:06:55Z"
    assert message == expected


def test_commit_message_round_trip() -> None:
    times = SnapshotTimes(
        fetched_at=datetime(2026, 10, 5, 12, 7, 31, tzinfo=UTC), feed_updated_at=FEED_UPDATED_AT
    )
    assert parse_commit_message(format_commit_message(times)) == times


def test_parse_commit_message_ignores_other_commits() -> None:
    assert parse_commit_message("compact 2026-10-05") is None
    assert parse_commit_message("build: start collecting station snapshots") is None
    assert parse_commit_message("") is None


def test_format_time_converts_to_utc() -> None:
    summer_time = timezone(timedelta(hours=2))
    moment = datetime(2026, 10, 5, 14, 7, 31, tzinfo=summer_time)
    assert format_time(moment) == "2026-10-05T12:07:31Z"
