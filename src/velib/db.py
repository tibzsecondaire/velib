"""SQL over the snapshots of a source with DuckDB: in memory, on the Parquet files, no server."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import duckdb
import polars as pl

from velib.archives import resolve_source
from velib.dataset import CACHE_DIR, STATIONS_FILE, local_copy

STEPS_VIEW = """
CREATE VIEW steps AS
WITH counts AS (
    SELECT
        station_id,
        fetched_at,
        CAST(mechanical AS INTEGER) AS mechanical,
        CAST(ebike AS INTEGER) AS ebike,
        CAST(docks AS INTEGER) AS docks
    FROM snapshots
)
SELECT
    station_id,
    fetched_at,
    timezone('Europe/Paris', fetched_at) AS local_time,
    mechanical,
    ebike,
    docks,
    mechanical + ebike AS bikes,
    date_diff('second', lag(fetched_at) OVER w, fetched_at) / 60.0 AS gap_minutes,
    mechanical + ebike - lag(mechanical + ebike) OVER w AS d_bikes,
    mechanical - lag(mechanical) OVER w AS d_mechanical,
    ebike - lag(ebike) OVER w AS d_ebike
FROM counts
WINDOW w AS (PARTITION BY station_id ORDER BY fetched_at)
"""


def connect(source: str | Path, *, cache_dir: Path = CACHE_DIR) -> duckdb.DuckDBPyConnection:
    """Opens an in-memory DuckDB database with views over the snapshots of a source.

    Views:
        snapshots: every row of daily/*.parquet, in the daily schema.
        stations: the station information, one row per station.
        steps: every snapshot with its Paris local time, the minutes since the previous
            snapshot of its station, and the change in bikes, mechanical and electric bikes.

    Args:
        source: Base URL of velib-data, or a local directory with the same layout. A remote
            source is copied into cache_dir first.
        cache_dir: Local cache for a remote source.

    Returns:
        The connection, ready for SQL.
    """
    directory = local_copy(source, cache_dir=cache_dir)
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    daily = _literal(directory / "daily" / "*.parquet")
    stations = _literal(directory / STATIONS_FILE)
    connection.execute(f"CREATE VIEW snapshots AS SELECT * FROM read_parquet({daily})")
    connection.execute(
        f"CREATE VIEW stations AS SELECT * FROM read_csv({stations}, "
        "types = {'station_code': 'VARCHAR'})"
    )
    connection.execute(STEPS_VIEW)
    return connection


def query(
    connection: duckdb.DuckDBPyConnection, sql: str, params: Mapping[str, object] | None = None
) -> pl.DataFrame:
    """Runs a query and returns its result as a polars DataFrame."""
    return pl.DataFrame(connection.sql(sql, params=params))


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-db command line.

    Args:
        argv: Arguments, sys.argv[1:] when None.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(description="Query Vélib' snapshots with SQL (DuckDB).")
    commands = parser.add_subparsers(dest="command", required=True)
    sql_parser = commands.add_parser(
        "sql", help="Run one query on the views snapshots, stations and steps."
    )
    sql_parser.add_argument("query", help="SQL query, in quotes.")
    sql_parser.add_argument(
        "--source",
        default="velib-data",
        help="velib-data, an imported archive such as kaggle, or a directory.",
    )
    sql_parser.add_argument(
        "--csv", type=Path, default=None, help="Also write the result to this CSV file."
    )
    args = parser.parse_args(argv)
    result = query(connect(resolve_source(args.source)), args.query)
    with pl.Config(tbl_rows=40, tbl_cols=-1, fmt_str_lengths=50):
        sys.stdout.write(f"{result}\n")
    if args.csv is not None:
        result.write_csv(args.csv)
    return 0


def _literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"
