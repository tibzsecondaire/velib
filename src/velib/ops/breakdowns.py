"""Out-of-service bikes and docks: bikes that never leave a station, docks that cannot be used."""

from __future__ import annotations

import duckdb
import polars as pl

from velib.db import query

MIN_SNAPSHOTS = 200
MIN_DEPARTURES = 10
MIN_DAYS = 3

IMMOBILE_SQL = """
WITH days AS (
    SELECT
        station_id,
        CAST(local_time AS DATE) AS day,
        count(*) AS snapshots,
        min(mechanical) AS mechanical_floor,
        min(ebike) AS ebike_floor,
        sum(greatest(-d_mechanical, 0)) AS mechanical_departures,
        sum(greatest(-d_ebike, 0)) AS ebike_departures
    FROM steps
    GROUP BY station_id, day
),
typed AS (
    SELECT
        station_id, day, snapshots, 'mechanical' AS kind,
        mechanical_floor AS floor, mechanical_departures AS departures
    FROM days
    UNION ALL
    SELECT
        station_id, day, snapshots, 'ebike' AS kind,
        ebike_floor AS floor, ebike_departures AS departures
    FROM days
),
flagged AS (
    SELECT
        *,
        day - CAST(row_number() OVER (PARTITION BY station_id, kind ORDER BY day) AS INTEGER)
            AS run
    FROM typed
    WHERE snapshots >= $min_snapshots AND departures >= $min_departures AND floor >= 1
)
SELECT
    f.station_id,
    s.name,
    f.kind,
    min(f.day) AS first_day,
    max(f.day) AS last_day,
    count(*) AS days,
    min(f.floor) AS bikes
FROM flagged AS f
LEFT JOIN stations AS s ON s.station_id = f.station_id
GROUP BY f.station_id, s.name, f.kind, f.run
HAVING count(*) >= $min_days
ORDER BY days DESC, bikes DESC, f.station_id, f.kind
"""

UNAVAILABLE_SQL = """
SELECT
    n.station_id,
    s.name,
    avg(greatest(s.capacity - n.mechanical - n.ebike - n.docks, 0)) AS unavailable,
    avg(CASE WHEN s.capacity - n.mechanical - n.ebike - n.docks > 0 THEN 1 ELSE 0 END)
        AS share_of_time
FROM snapshots AS n
JOIN stations AS s ON s.station_id = n.station_id
GROUP BY n.station_id, s.name
ORDER BY unavailable DESC, n.station_id
"""


def immobile_bikes(
    connection: duckdb.DuckDBPyConnection,
    *,
    min_snapshots: int = MIN_SNAPSHOTS,
    min_departures: int = MIN_DEPARTURES,
    min_days: int = MIN_DAYS,
) -> pl.DataFrame:
    """Finds bikes that stay in a station for days while bikes of their type keep leaving it.

    A Paris day counts when a station has at least min_snapshots snapshots and min_departures
    departures of the type. Its floor is the smallest number of bikes of that type during the
    day. At least min_days consecutive days with a floor of 1 or more make an immobilisation
    of as many bikes as the smallest floor: probably broken bikes or, for electric bikes,
    discharged ones.

    Args:
        connection: Connection from velib.db.connect.
        min_snapshots: Snapshots that make a full day.
        min_departures: Departures of the type that make an active day.
        min_days: Shortest immobilisation, in consecutive days.

    Returns:
        One row per immobilisation: station_id, name, kind (mechanical or ebike), first_day,
        last_day, days and bikes, longest first.
    """
    params = {
        "min_snapshots": min_snapshots,
        "min_departures": min_departures,
        "min_days": min_days,
    }
    return query(connection, IMMOBILE_SQL, params)


def immobile_per_day(immobilisations: pl.DataFrame) -> pl.DataFrame:
    """Immobile bikes of each type over the whole city, per day: day, kind, bikes, stations."""
    return (
        immobilisations.with_columns(pl.date_ranges("first_day", "last_day").alias("day"))
        .explode("day", empty_as_null=False)
        .group_by("day", "kind")
        .agg(pl.col("bikes").sum(), pl.len().alias("stations"))
        .sort("day", "kind")
    )


def has_real_docks(connection: duckdb.DuckDBPyConnection) -> bool:
    """Whether the free docks come from the feed, as in velib-data, not from an estimate."""
    sql = "SELECT count(feed_updated_at) > 0 AS real_docks FROM snapshots"
    return bool(query(connection, sql).item())


def unavailable_docks(connection: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Docks that hold no rentable bike and take no bike back, per station.

    The feed counts neither broken docks nor disabled bikes. Capacity minus bikes minus free
    docks estimates them, clipped at 0 where the capacity is out of date.

    Returns:
        One row per station: station_id, name, unavailable (mean docks) and share_of_time
        (share of the snapshots with at least one), most affected first.
    """
    return query(connection, UNAVAILABLE_SQL)
