"""Fill-rate data for the daily heatmap: one value per station and per time slot."""

from __future__ import annotations

from datetime import date

import polars as pl

PARIS_TIME_ZONE = "Europe/Paris"


def fill_rate_by_slot(
    frame: pl.DataFrame, day: date, *, slot: str = "15m", time_zone: str = PARIS_TIME_ZONE
) -> pl.DataFrame:
    """Averages the fill rate of each station per time slot of one local day.

    The fill rate is bikes / (bikes + free docks). It is null when a station has
    neither bikes nor free docks.

    Args:
        frame: Snapshot rows with fetched_at (UTC), station_id, mechanical, ebike and docks.
        day: Local day to keep.
        slot: Slot length, as a polars duration string.
        time_zone: Time zone that defines the local day and the slots.

    Returns:
        Columns station_id, slot (local time) and fill_rate, sorted by station and slot.
    """
    bikes = pl.col("mechanical").cast(pl.Int32) + pl.col("ebike").cast(pl.Int32)
    total = bikes + pl.col("docks").cast(pl.Int32)
    local_time = pl.col("fetched_at").dt.convert_time_zone(time_zone)
    return (
        frame.filter(local_time.dt.date() == day)
        .with_columns(
            local_time.dt.truncate(slot).alias("slot"),
            pl.when(total > 0).then(bikes / total).alias("fill_rate"),
        )
        .group_by("station_id", "slot")
        .agg(pl.col("fill_rate").mean())
        .sort("station_id", "slot")
    )
