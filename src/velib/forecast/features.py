"""Features for availability forecasting: a 5-minute grid, station profiles and examples."""

from __future__ import annotations

import math

import polars as pl

STEP_MINUTES = 5
PARIS_TIME_ZONE = "Europe/Paris"
LAGS = (1, 3, 6, 12)
WEATHER_COLUMNS = ("temp_c", "precip_mm", "wind_mps")
FEATURES = (
    "bikes",
    "ebike",
    "docks",
    "total",
    "fill",
    *(f"bikes_lag{lag}" for lag in LAGS),
    *(f"delta{lag}" for lag in LAGS),
    "hour_sin",
    "hour_cos",
    "target_hour_sin",
    "target_hour_cos",
    "weekday",
    "is_weekend",
    "profile_now",
    "profile_target",
    "profile_delta",
    "profile_all_target",
    "lat",
    "lon",
    *WEATHER_COLUMNS,
)


def build_grid(frame: pl.DataFrame) -> pl.DataFrame:
    """Puts snapshots on a regular 5-minute grid, one row per station and slot.

    Args:
        frame: Daily-schema rows: fetched_at, station_id, mechanical, ebike, docks and statuses.

    Returns:
        Columns station_id, t (slot number since 1970), time (UTC start of the slot), bikes,
        ebike, docks, total (bikes + docks) and is_open, sorted by station and slot. When a slot
        has several snapshots, the last one wins.
    """
    bikes = pl.col("mechanical").cast(pl.Int32) + pl.col("ebike").cast(pl.Int32)
    return (
        frame.sort("fetched_at")
        .with_columns(pl.col("fetched_at").dt.truncate(f"{STEP_MINUTES}m").alias("time"))
        .unique(subset=["station_id", "time"], keep="last", maintain_order=True)
        .select(
            "station_id",
            (pl.col("time").dt.epoch("s") // (STEP_MINUTES * 60)).alias("t"),
            "time",
            bikes.alias("bikes"),
            pl.col("ebike").cast(pl.Int32),
            pl.col("docks").cast(pl.Int32),
            (bikes + pl.col("docks").cast(pl.Int32)).alias("total"),
            (pl.col("is_installed") & pl.col("is_renting")).alias("is_open"),
        )
        .sort("station_id", "t")
    )


def calendar(time: pl.Expr, prefix: str = "") -> list[pl.Expr]:
    """Hour of day, its sine and cosine, weekday and day type of a UTC time, in Paris time."""
    local = time.dt.convert_time_zone(PARIS_TIME_ZONE)
    angle = (local.dt.hour() + local.dt.minute() / 60) * (2 * math.pi / 24)
    return [
        local.dt.hour().alias(f"{prefix}hour"),
        angle.sin().alias(f"{prefix}hour_sin"),
        angle.cos().alias(f"{prefix}hour_cos"),
        local.dt.weekday().alias(f"{prefix}weekday"),
        (local.dt.weekday() >= 6).alias(f"{prefix}is_weekend"),
    ]


def station_profiles(train: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Mean bikes of each station by hour and day type, and by hour over all days.

    Args:
        train: Grid rows of the training period only.

    Returns:
        A table keyed by station_id, is_weekend and hour, with a profile column, and a table
        keyed by station_id and hour, with a profile_all column.
    """
    rows = train.filter(pl.col("is_open")).with_columns(calendar(pl.col("time")))
    by_day_type = rows.group_by("station_id", "is_weekend", "hour").agg(
        pl.col("bikes").mean().alias("profile")
    )
    all_days = rows.group_by("station_id", "hour").agg(pl.col("bikes").mean().alias("profile_all"))
    return by_day_type, all_days


def build_examples(
    grid: pl.DataFrame,
    horizon_minutes: int,
    profiles: tuple[pl.DataFrame, pl.DataFrame],
    stations: pl.DataFrame | None = None,
    weather: pl.DataFrame | None = None,
    origins: pl.Expr | None = None,
) -> pl.DataFrame:
    """Builds one example per station and slot: features at t and bikes at t + horizon.

    Only pairs where the station is open at t and at t + horizon are kept.

    Args:
        grid: Output of build_grid.
        horizon_minutes: Forecast horizon, a multiple of 5 minutes.
        profiles: Output of station_profiles, computed on the training period.
        stations: Station information with station_id, lat and lon.
        weather: Weather by slot, with time_bin and WEATHER_COLUMNS.
        origins: Filter on the grid rows used as forecast origins.

    Returns:
        The examples, with FEATURES, target (bikes at t + horizon), time and target_time.
    """
    steps = horizon_minutes // STEP_MINUTES
    by_day_type, all_days = profiles
    keep = pl.col("is_open") if origins is None else pl.col("is_open") & origins
    future = grid.filter(pl.col("is_open")).select(
        "station_id", (pl.col("t") - steps).alias("t"), pl.col("bikes").alias("target")
    )
    examples = grid.filter(keep).join(future, on=["station_id", "t"], how="inner")
    for lag in LAGS:
        past = grid.select(
            "station_id", (pl.col("t") + lag).alias("t"), pl.col("bikes").alias(f"bikes_lag{lag}")
        )
        examples = examples.join(past, on=["station_id", "t"], how="left")
    examples = (
        examples.with_columns(
            *calendar(pl.col("time")),
            (pl.col("time") + pl.duration(minutes=horizon_minutes)).alias("target_time"),
            pl.when(pl.col("total") > 0).then(pl.col("bikes") / pl.col("total")).alias("fill"),
            *[(pl.col("bikes") - pl.col(f"bikes_lag{lag}")).alias(f"delta{lag}") for lag in LAGS],
        )
        .with_columns(calendar(pl.col("target_time"), prefix="target_"))
        .join(
            by_day_type.rename({"profile": "profile_now"}),
            on=["station_id", "is_weekend", "hour"],
            how="left",
        )
        .join(
            by_day_type.rename(
                {
                    "is_weekend": "target_is_weekend",
                    "hour": "target_hour",
                    "profile": "profile_target",
                }
            ),
            on=["station_id", "target_is_weekend", "target_hour"],
            how="left",
        )
        .join(
            all_days.rename({"hour": "target_hour", "profile_all": "profile_all_target"}),
            on=["station_id", "target_hour"],
            how="left",
        )
        .with_columns((pl.col("profile_target") - pl.col("profile_now")).alias("profile_delta"))
    )
    if stations is None:
        examples = examples.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("lat"), pl.lit(None, dtype=pl.Float64).alias("lon")
        )
    else:
        examples = examples.join(
            stations.select("station_id", "lat", "lon"), on="station_id", how="left"
        )
    if weather is None:
        examples = examples.with_columns(
            *(pl.lit(None, dtype=pl.Float64).alias(name) for name in WEATHER_COLUMNS)
        )
    else:
        examples = examples.join(
            weather.select("time_bin", *WEATHER_COLUMNS).rename({"time_bin": "time"}),
            on="time",
            how="left",
        )
    return examples.sort("station_id", "t")
