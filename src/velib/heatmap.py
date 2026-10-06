"""Fill-rate data for the heatmaps: per station and time slot, and for the whole city."""

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
    local_day = _local_time(time_zone).dt.date()
    return fill_rate_over_time(frame.filter(local_day == day), slot=slot, time_zone=time_zone)


def fill_rate_over_time(
    frame: pl.DataFrame, *, slot: str = "1h", time_zone: str = PARIS_TIME_ZONE
) -> pl.DataFrame:
    """Averages the fill rate of each station per local time slot, over the whole frame.

    Args:
        frame: Snapshot rows with fetched_at (UTC), station_id, mechanical, ebike and docks.
        slot: Slot length, as a polars duration string.
        time_zone: Time zone of the slots.

    Returns:
        Columns station_id, slot (local time) and fill_rate, sorted by station and slot.
    """
    return (
        frame.with_columns(
            _local_time(time_zone).dt.truncate(slot).alias("slot"),
            _fill_rate().alias("fill_rate"),
        )
        .group_by("station_id", "slot")
        .agg(pl.col("fill_rate").mean())
        .sort("station_id", "slot")
    )


def city_rhythm(
    frame: pl.DataFrame, *, slot: str = "1h", time_zone: str = PARIS_TIME_ZONE
) -> pl.DataFrame:
    """Mean fill rate and share of empty stations over the open stations, per local slot.

    Args:
        frame: Snapshot rows with fetched_at (UTC), mechanical, ebike, docks, is_installed and
            is_renting.
        slot: Slot length, as a polars duration string.
        time_zone: Time zone of the slots.

    Returns:
        Columns slot (local time), day, time_of_day ("HH:MM"), fill_rate and empty_share,
        sorted by slot.
    """
    return (
        frame.filter(pl.col("is_installed") & pl.col("is_renting"))
        .with_columns(
            _local_time(time_zone).dt.truncate(slot).alias("slot"),
            _fill_rate().alias("fill_rate"),
            (_bikes() == 0).alias("empty"),
        )
        .group_by("slot")
        .agg(pl.col("fill_rate").mean(), pl.col("empty").mean().alias("empty_share"))
        .with_columns(
            pl.col("slot").dt.date().alias("day"),
            pl.col("slot").dt.strftime("%H:%M").alias("time_of_day"),
        )
        .select("slot", "day", "time_of_day", "fill_rate", "empty_share")
        .sort("slot")
    )


def _bikes() -> pl.Expr:
    return pl.col("mechanical").cast(pl.Int32) + pl.col("ebike").cast(pl.Int32)


def _fill_rate() -> pl.Expr:
    total = _bikes() + pl.col("docks").cast(pl.Int32)
    return pl.when(total > 0).then(_bikes() / total)


def _local_time(time_zone: str) -> pl.Expr:
    return pl.col("fetched_at").dt.convert_time_zone(time_zone)
