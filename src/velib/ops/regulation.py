"""Rebalancing: bikes that the operator adds to or removes from a station in one go."""

from __future__ import annotations

from typing import Any

import duckdb
import polars as pl

from velib.db import query

THRESHOLD = 8
MAX_GAP_MINUTES = 7

OPERATIONS_SQL = """
WITH marked AS (
    SELECT
        station_id,
        fetched_at,
        local_time,
        bikes,
        d_bikes,
        CASE
            WHEN gap_minutes <= $max_gap AND d_bikes >= $threshold THEN 1
            WHEN gap_minutes <= $max_gap AND d_bikes <= -$threshold THEN -1
            ELSE 0
        END AS direction
    FROM steps
),
previous AS (
    SELECT *, lag(direction) OVER (PARTITION BY station_id ORDER BY fetched_at) AS before
    FROM marked
),
numbered AS (
    SELECT
        *,
        sum(CASE WHEN direction <> 0 AND direction IS DISTINCT FROM before THEN 1 ELSE 0 END)
            OVER (PARTITION BY station_id ORDER BY fetched_at) AS operation
    FROM previous
)
SELECT
    n.station_id,
    s.name,
    min(n.local_time) AS first_seen,
    max(n.local_time) AS last_seen,
    CAST(sum(n.d_bikes) AS INTEGER) AS bikes_moved,
    arg_min(n.bikes - n.d_bikes, n.fetched_at) AS bikes_before,
    arg_max(n.bikes, n.fetched_at) AS bikes_after,
    s.capacity
FROM numbered AS n
LEFT JOIN stations AS s ON s.station_id = n.station_id
WHERE n.direction <> 0
GROUP BY n.station_id, s.name, s.capacity, n.operation
ORDER BY first_seen, n.station_id
"""

ACTIVITY_SQL = """
SELECT CAST(hour(local_time) AS INTEGER) AS hour, CAST(sum(abs(d_bikes)) AS BIGINT) AS moves
FROM steps
WHERE gap_minutes <= $max_gap
GROUP BY hour
ORDER BY hour
"""


def detect_operations(
    connection: duckdb.DuckDBPyConnection,
    *,
    threshold: int = THRESHOLD,
    max_gap_minutes: float = MAX_GAP_MINUTES,
) -> pl.DataFrame:
    """Finds the rebalancing operations in the steps view.

    A step belongs to an operation when the bikes of a station change by at least threshold
    between two snapshots at most max_gap_minutes apart. Consecutive steps in the same
    direction form one operation.

    Args:
        connection: Connection from velib.db.connect.
        threshold: Smallest change, in bikes.
        max_gap_minutes: Largest time between the two snapshots of a step.

    Returns:
        One row per operation: station_id, name, first_seen and last_seen (Paris local time),
        bikes_moved (positive when bikes are added), bikes_before, bikes_after and capacity.
    """
    params = {"threshold": threshold, "max_gap": max_gap_minutes}
    return query(connection, OPERATIONS_SQL, params)


def activity_by_hour(connection: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Bikes moved by everyone, riders and operator, per Paris hour: columns hour and moves."""
    return query(connection, ACTIVITY_SQL, {"max_gap": MAX_GAP_MINUTES})


def summarize_by_day(operations: pl.DataFrame) -> pl.DataFrame:
    """Operations, bikes added and bikes removed per Paris day."""
    return (
        operations.group_by(pl.col("first_seen").dt.date().alias("day"))
        .agg(pl.len().alias("operations"), *_added_and_removed())
        .sort("day")
    )


def hourly_shares(operations: pl.DataFrame, activity: pl.DataFrame) -> pl.DataFrame:
    """Share of the operations and share of all bike moves per Paris hour, in percent."""
    counts = operations.group_by(pl.col("first_seen").dt.hour().cast(pl.Int32).alias("hour")).agg(
        pl.len().alias("operations")
    )
    return (
        activity.join(counts, on="hour", how="left")
        .with_columns(pl.col("operations").fill_null(0))
        .select(
            "hour",
            (pl.col("operations") / pl.col("operations").sum() * 100).alias("operations_pct"),
            (pl.col("moves") / pl.col("moves").sum() * 100).alias("moves_pct"),
        )
        .sort("hour")
    )


def top_stations(operations: pl.DataFrame, *, limit: int = 10) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Stations that receive the most bikes, and stations that lose the most."""
    totals = operations.group_by("station_id", "name").agg(
        pl.len().alias("operations"), *_added_and_removed()
    )
    return (
        totals.sort(["bikes_added", "station_id"], descending=[True, False]).head(limit),
        totals.sort(["bikes_removed", "station_id"], descending=[True, False]).head(limit),
    )


def fill_before(operations: pl.DataFrame) -> dict[str, Any]:
    """Mean fill rate of the stations just before additions and just before removals."""
    fill = pl.col("bikes_before") / pl.col("capacity")
    return operations.select(
        fill.filter(pl.col("bikes_moved") > 0).mean().alias("additions"),
        fill.filter(pl.col("bikes_moved") < 0).mean().alias("removals"),
    ).row(0, named=True)


def _added_and_removed() -> list[pl.Expr]:
    return [
        pl.col("bikes_moved").clip(lower_bound=0).sum().alias("bikes_added"),
        (-pl.col("bikes_moved").clip(upper_bound=0)).sum().alias("bikes_removed"),
    ]
