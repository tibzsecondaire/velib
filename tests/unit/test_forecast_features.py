"""Tests for the forecasting features."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from velib.forecast.features import FEATURES, build_examples, build_grid, station_profiles

START = datetime(2025, 12, 6, 9, 0, tzinfo=UTC)


def _frame(slots: int, bikes_of: dict[int, int] | None = None) -> pl.DataFrame:
    rows = []
    for station_id in (1, 2):
        for slot in range(slots):
            bikes = (bikes_of or {}).get(slot, slot)
            rows.append(
                {
                    "fetched_at": START + timedelta(minutes=5 * slot, seconds=30),
                    "station_id": station_id,
                    "mechanical": bikes,
                    "ebike": 0,
                    "docks": 40 - bikes,
                    "is_installed": True,
                    "is_renting": station_id == 1 or slot != 2,
                    "is_returning": True,
                }
            )
    return pl.DataFrame(rows, schema_overrides={"fetched_at": pl.Datetime("us", "UTC")})


def test_build_grid_puts_snapshots_on_5_minute_slots() -> None:
    grid = build_grid(_frame(4))
    assert grid.height == 8
    assert grid.get_column("time").to_list()[0] == START
    assert grid.get_column("total").unique().to_list() == [40]
    first = grid.filter(pl.col("station_id") == 1).get_column("t").to_list()
    assert [t - first[0] for t in first] == [0, 1, 2, 3]
    assert grid.filter(pl.col("station_id") == 2).get_column("is_open").to_list() == [
        True,
        True,
        False,
        True,
    ]


def test_build_examples_aligns_targets_lags_and_calendar() -> None:
    grid = build_grid(_frame(30))
    profiles = station_profiles(grid)
    examples = build_examples(grid, 15, profiles).filter(pl.col("station_id") == 1)
    row = examples.filter(pl.col("bikes") == 20).row(0, named=True)
    assert row["target"] == 23
    assert row["bikes_lag1"] == 19
    assert row["bikes_lag12"] == 8
    assert row["delta3"] == 3
    assert row["is_weekend"] is True
    assert row["target_hour"] == 11
    assert set(FEATURES) <= set(examples.columns)


def test_build_examples_skips_closed_stations_at_t_and_t_plus_h() -> None:
    grid = build_grid(_frame(10))
    examples = build_examples(grid, 5, station_profiles(grid)).filter(pl.col("station_id") == 2)
    assert 1 not in examples.get_column("bikes").to_list()
    assert 2 not in examples.get_column("bikes").to_list()


def test_profiles_use_only_the_rows_given() -> None:
    grid = build_grid(
        _frame(24, bikes_of=dict.fromkeys(range(12), 10) | dict.fromkeys(range(12, 24), 30))
    )
    train = grid.filter(pl.col("time") < START + timedelta(hours=1))
    by_day_type, all_days = station_profiles(train)
    assert by_day_type.filter(pl.col("station_id") == 1).get_column("profile").to_list() == [10.0]
    assert all_days.filter(pl.col("station_id") == 1).get_column("hour").to_list() == [10]


def test_build_examples_can_keep_only_some_origins() -> None:
    grid = build_grid(_frame(30))
    examples = build_examples(grid, 15, station_profiles(grid), origins=pl.col("t") % 3 == 0)
    assert (examples.get_column("t") % 3).unique().to_list() == [0]
