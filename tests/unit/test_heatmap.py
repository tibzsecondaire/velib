"""Tests for the fill-rate heatmap data."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl

from velib.heatmap import fill_rate_by_slot

SCHEMA = {
    "fetched_at": pl.Datetime("us", "UTC"),
    "station_id": pl.Int64(),
    "mechanical": pl.Int16(),
    "ebike": pl.Int16(),
    "docks": pl.Int16(),
}


def test_fill_rate_is_averaged_per_paris_time_slot() -> None:
    frame = pl.DataFrame(
        [
            (datetime(2026, 10, 4, 21, 59, tzinfo=UTC), 1, 5, 0, 5),
            (datetime(2026, 10, 4, 22, 2, tzinfo=UTC), 1, 1, 1, 2),
            (datetime(2026, 10, 4, 22, 7, tzinfo=UTC), 1, 3, 0, 1),
            (datetime(2026, 10, 4, 22, 16, tzinfo=UTC), 1, 0, 0, 0),
        ],
        schema=SCHEMA,
        orient="row",
    )
    rates = fill_rate_by_slot(frame, date(2026, 10, 5))
    assert rates.get_column("slot").dt.strftime("%H:%M").to_list() == ["00:00", "00:15"]
    assert rates.get_column("fill_rate").to_list() == [0.625, None]
