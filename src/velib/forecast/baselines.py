"""Reference forecasts to compare the model with."""

from __future__ import annotations

from collections.abc import Callable

import polars as pl


def persistence(examples: pl.DataFrame) -> pl.Series:
    """Bikes at t + horizon = bikes at t."""
    return examples.get_column("bikes").cast(pl.Float64).alias("persistence")


def profile(examples: pl.DataFrame) -> pl.Series:
    """Usual bikes of the station at the hour and day type of t + horizon."""
    return examples.select(
        pl.coalesce(pl.col("profile_target"), pl.col("bikes").cast(pl.Float64)).alias("profile")
    ).to_series()


def adjusted_persistence(examples: pl.DataFrame) -> pl.Series:
    """Bikes at t plus the usual change between t and t + horizon, within the capacity."""
    return examples.select(
        (pl.col("bikes") + pl.col("profile_delta").fill_null(0.0))
        .clip(0, pl.col("total"))
        .cast(pl.Float64)
        .alias("adjusted persistence")
    ).to_series()


BASELINES: dict[str, Callable[[pl.DataFrame], pl.Series]] = {
    "persistence": persistence,
    "profile": profile,
    "adjusted persistence": adjusted_persistence,
}
