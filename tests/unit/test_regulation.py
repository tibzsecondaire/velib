"""Tests for the detection of rebalancing operations."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tests.unit.sources import write_source
from velib.db import connect
from velib.ops.regulation import (
    activity_by_hour,
    detect_operations,
    fill_before,
    hourly_shares,
    summarize_by_day,
    top_stations,
)

T0 = datetime(2025, 12, 9, 2, 0, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _connection(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    station_one = [2, 2, 12, 20, 21, 9]
    rows = [(_at(5 * step), 1, bikes, 0, 30 - bikes) for step, bikes in enumerate(station_one)]
    rows += [(_at(0), 2, 5, 0, 15), (_at(20), 2, 13, 0, 7)]
    return connect(write_source(tmp_path / "source", rows))


def test_detect_operations_merges_consecutive_steps_in_the_same_direction(
    tmp_path: Path,
) -> None:
    operations = detect_operations(_connection(tmp_path))
    columns = ["station_id", "name", "bikes_moved", "bikes_before", "bikes_after", "capacity"]
    assert operations.select(columns).rows() == [
        (1, "Alpha", 18, 2, 20, 30),
        (1, "Alpha", -12, 21, 9, 30),
    ]
    assert operations.get_column("first_seen").dt.strftime("%H:%M").to_list() == ["03:10", "03:25"]
    assert operations.get_column("last_seen").dt.strftime("%H:%M").to_list() == ["03:15", "03:25"]


def test_detect_operations_follows_the_threshold(tmp_path: Path) -> None:
    operations = detect_operations(_connection(tmp_path), threshold=10)
    assert operations.get_column("bikes_moved").to_list() == [10, -12]


def test_summaries_count_operations_per_day_hour_and_station(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    operations = detect_operations(connection)
    assert summarize_by_day(operations).rows() == [(date(2025, 12, 9), 2, 18, 12)]
    shares = hourly_shares(operations, activity_by_hour(connection))
    assert shares.filter(pl.col("hour") == 3).row(0) == (3, 100.0, 100.0)
    receivers, givers = top_stations(operations)
    assert receivers.select("station_id", "bikes_added").row(0) == (1, 18)
    assert givers.select("station_id", "bikes_removed").row(0) == (1, 12)
    fills = fill_before(operations)
    assert fills["additions"] == pytest.approx(2 / 30)
    assert fills["removals"] == pytest.approx(21 / 30)
