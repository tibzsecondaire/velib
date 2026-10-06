"""Tests for the fill-rate heatmap data."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from velib.heatmap import city_rhythm, fill_rate_by_slot, fill_rate_over_time

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


def test_fill_rate_over_time_keeps_every_day() -> None:
    frame = pl.DataFrame(
        [
            (datetime(2026, 10, 4, 21, 59, tzinfo=UTC), 1, 5, 0, 5),
            (datetime(2026, 10, 5, 8, 10, tzinfo=UTC), 1, 2, 0, 6),
        ],
        schema=SCHEMA,
        orient="row",
    )
    rates = fill_rate_over_time(frame, slot="1h")
    assert rates.get_column("slot").dt.strftime("%d %H:%M").to_list() == ["04 23:00", "05 10:00"]
    assert rates.get_column("fill_rate").to_list() == [0.5, 0.25]


def test_fill_rate_over_time_averages_the_available_bikes() -> None:
    frame = pl.DataFrame(
        [
            (datetime(2026, 10, 5, 8, 0, tzinfo=UTC), 1, 5, 0, 5),
            (datetime(2026, 10, 5, 8, 20, tzinfo=UTC), 1, 1, 2, 7),
            (datetime(2026, 10, 5, 8, 40, tzinfo=UTC), 1, 0, 0, 0),
        ],
        schema=SCHEMA,
        orient="row",
    )
    rates = fill_rate_over_time(frame, slot="1h")
    assert rates.get_column("bikes").to_list() == pytest.approx([8 / 3])


def test_city_rhythm_averages_open_stations_per_slot() -> None:
    frame = pl.DataFrame(
        [
            (datetime(2026, 10, 5, 6, 5, tzinfo=UTC), 1, 0, 0, 10, True),
            (datetime(2026, 10, 5, 6, 5, tzinfo=UTC), 2, 4, 2, 4, True),
            (datetime(2026, 10, 5, 6, 35, tzinfo=UTC), 3, 9, 0, 1, False),
        ],
        schema=SCHEMA | {"is_renting": pl.Boolean()},
        orient="row",
    ).with_columns(pl.lit(True).alias("is_installed"))
    rhythm = city_rhythm(frame, slot="1h")
    row = rhythm.row(0, named=True)
    assert rhythm.height == 1
    assert row["day"] == date(2026, 10, 5)
    assert row["time_of_day"] == "08:00"
    assert row["fill_rate"] == 0.3
    assert row["empty_share"] == 0.5
