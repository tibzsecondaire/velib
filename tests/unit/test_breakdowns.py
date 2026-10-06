"""Tests for the immobile bikes and the out-of-service docks."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from tests.unit.sources import Row, write_source
from velib.db import connect
from velib.ops.breakdowns import (
    has_real_docks,
    immobile_bikes,
    immobile_per_day,
    unavailable_docks,
)

DAYS = [9, 10, 11]
HOURS = [8, 12, 18]


def _immobile_connection(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    rows: list[Row] = []
    for day in DAYS:
        station_one_mechanical = [0, 3, 0] if day == 11 else [1, 3, 1]
        for index, hour in enumerate(HOURS):
            moment = datetime(2025, 12, day, hour, tzinfo=UTC)
            rows.append((moment, 1, station_one_mechanical[index], [1, 3, 1][index], 5))
            rows.append((moment, 2, [2, 4, 2][index], [0, 1, 0][index], 5))
    return connect(write_source(tmp_path / "source", rows))


def test_immobile_bikes_needs_consecutive_active_days_with_a_floor(tmp_path: Path) -> None:
    immobilisations = immobile_bikes(
        _immobile_connection(tmp_path), min_snapshots=3, min_departures=2, min_days=3
    )
    first, last = date(2025, 12, 9), date(2025, 12, 11)
    assert immobilisations.rows() == [
        (2, "Bravo", "mechanical", first, last, 3, 2),
        (1, "Alpha", "ebike", first, last, 3, 1),
    ]
    per_day = immobile_per_day(immobilisations)
    assert per_day.head(2).rows() == [(first, "ebike", 1, 1), (first, "mechanical", 2, 1)]


def test_unavailable_docks_clips_at_zero_and_needs_real_docks(tmp_path: Path) -> None:
    moments = [datetime(2025, 12, 9, 8, tzinfo=UTC), datetime(2025, 12, 9, 9, tzinfo=UTC)]
    rows: list[Row] = [
        (moments[0], 1, 10, 5, 12),
        (moments[1], 1, 10, 5, 15),
        (moments[0], 2, 15, 5, 3),
    ]
    live = connect(write_source(tmp_path / "live", rows))
    assert has_real_docks(live)
    assert unavailable_docks(live).rows() == [(1, "Alpha", 1.5, 0.5), (2, "Bravo", 0.0, 0.0)]
    archive = connect(write_source(tmp_path / "archive", rows, real_docks=False))
    assert not has_real_docks(archive)
