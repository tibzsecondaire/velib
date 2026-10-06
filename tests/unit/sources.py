"""Tiny sources in the layout of velib-data, for the database and operations tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

STATIONS = pl.DataFrame(
    {
        "station_id": [1, 2],
        "station_code": ["00001", "00002"],
        "name": ["Alpha", "Bravo"],
        "lat": [48.85, 48.86],
        "lon": [2.35, 2.36],
        "capacity": [30, 20],
    }
)

Row = tuple[datetime, int, int, int, int]


def write_source(
    directory: Path,
    rows: list[Row],
    *,
    stations: pl.DataFrame = STATIONS,
    real_docks: bool = True,
) -> Path:
    """Writes snapshots (fetched_at, station_id, mechanical, ebike, docks) as one daily file.

    Args:
        directory: Directory to create, with daily/ and stations/.
        rows: Snapshots, fetched_at in UTC.
        stations: Station information.
        real_docks: Whether the free docks come from the feed, as in velib-data, or from an
            estimate, as in the archives, whose feed_updated_at is empty.

    Returns:
        The directory.
    """
    feed_time = pl.col("fetched_at") if real_docks else pl.lit(None, dtype=pl.Datetime("us", "UTC"))
    frame = pl.DataFrame(
        rows,
        schema={
            "fetched_at": pl.Datetime("us", "UTC"),
            "station_id": pl.Int64(),
            "mechanical": pl.Int16(),
            "ebike": pl.Int16(),
            "docks": pl.Int16(),
        },
        orient="row",
    ).with_columns(
        feed_time.alias("feed_updated_at"),
        pl.lit(True).alias("is_installed"),
        pl.lit(True).alias("is_renting"),
        pl.lit(True).alias("is_returning"),
        pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("last_reported"),
    )
    (directory / "daily").mkdir(parents=True)
    (directory / "stations").mkdir()
    frame.write_parquet(directory / "daily" / "2025-12-09.parquet")
    stations.write_csv(directory / "stations" / "station_information.csv")
    return directory
