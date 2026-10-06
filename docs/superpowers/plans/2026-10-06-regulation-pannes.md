# Régulation et pannes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the snapshots behind DuckDB views and detect rebalancing operations, immobile bikes and out-of-service docks, with a report and a map.

**Architecture:** `velib.db` opens an in-memory DuckDB connection with the views `snapshots`, `stations` and `steps` over the Parquet files of a source (a local copy for velib-data). `velib.ops.regulation` and `velib.ops.breakdowns` are SQL queries on those views that return polars frames. `velib.ops.report` turns them into a Markdown report and a standalone MapLibre page, and `velib.ops.cli` writes both.

**Tech Stack:** DuckDB 1.5 (group `db`), polars (reads DuckDB results through the Arrow stream interface, no pyarrow), MapLibre GL JS 6.12 and OpenFreeMap like the replay map, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-regulation-pannes-design.md`

## Global Constraints

- DuckDB lives in the dependency group `db`; run with `uv run --group db …`. The collector is not touched.
- Paris local time (`Europe/Paris`) for every hour and day shown; snapshots stay in UTC.
- Thresholds: operation step of 8 bikes or more, snapshots at most 7 minutes apart; a counted day has at least 200 snapshots and 10 departures of the type; an immobilisation lasts at least 3 consecutive days.
- Out-of-service docks only for sources with `feed_updated_at` (velib-data).
- Reports in English like the forecast report; the map page in French like the replay map.
- Code style of the repo: ruff (100 columns, Google docstrings, no print), mypy strict.

---

### Task 1: DuckDB views over a source

**Files:**
- Modify: `pyproject.toml` (group `db`, script `velib-db`)
- Modify: `src/velib/dataset.py` (add `local_copy`)
- Create: `src/velib/db.py`
- Create: `tests/unit/sources.py`, `tests/unit/test_db.py`
- Modify: `tests/unit/test_dataset.py`

**Interfaces:**
- Produces: `dataset.local_copy(source, *, cache_dir, client) -> Path`; `db.connect(source, *, cache_dir) -> duckdb.DuckDBPyConnection`; `db.query(connection, sql, params=None) -> pl.DataFrame`; `db.main(argv) -> int`; test helper `tests.unit.sources.write_source(directory, rows, *, stations, real_docks) -> Path`.

- [ ] **Step 1: Add DuckDB and the command**

Run: `uv add --group db duckdb`, then add to `[project.scripts]`:

```toml
velib-db = "velib.db:main"
```

- [ ] **Step 2: Write the test helper and the failing tests**

<!-- file: tests/unit/sources.py -->
```python
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
```

<!-- file: tests/unit/test_db.py -->
```python
"""Tests for the DuckDB views over a source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.unit.sources import write_source
from velib.db import connect, main, query

T0 = datetime(2025, 12, 9, 7, 0, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_connect_exposes_snapshots_stations_and_steps(tmp_path: Path) -> None:
    source = write_source(
        tmp_path / "source",
        [
            (_at(0), 1, 2, 1, 27),
            (_at(5), 1, 4, 0, 26),
            (_at(20), 1, 1, 0, 29),
            (_at(0), 2, 5, 5, 10),
        ],
    )
    connection = connect(source)
    assert query(connection, "SELECT count(*) AS n FROM snapshots").item() == 4
    names = query(connection, "SELECT name FROM stations ORDER BY station_id")
    assert names.to_series().to_list() == ["Alpha", "Bravo"]
    steps = query(connection, "SELECT * FROM steps WHERE station_id = 1 ORDER BY fetched_at")
    assert steps.get_column("local_time").dt.strftime("%H:%M").to_list() == [
        "08:00",
        "08:05",
        "08:20",
    ]
    assert steps.get_column("gap_minutes").to_list() == [None, 5.0, 15.0]
    assert steps.get_column("d_bikes").to_list() == [None, 1, -3]
    assert steps.get_column("d_mechanical").to_list() == [None, 2, -3]
    assert steps.get_column("d_ebike").to_list() == [None, -1, 0]


def test_velib_db_sql_prints_the_result_and_writes_a_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = write_source(tmp_path / "source", [(T0, 1, 2, 1, 27)])
    out = tmp_path / "result.csv"
    sql = "SELECT count(*) AS snapshot_count FROM snapshots"
    assert main(["sql", "--source", str(source), "--csv", str(out), sql]) == 0
    assert "snapshot_count" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == "snapshot_count\n1\n"
```

Append to `tests/unit/test_dataset.py`:

```python
def test_local_copy_downloads_the_missing_days_once(tmp_path: Path) -> None:
    requested: list[str] = []
    files = {
        "daily/index.csv": b"date\n2026-10-05\n",
        "daily/2026-10-05.parquet": _parquet_bytes(),
        "stations/station_information.csv": b"station_id,station_code,name,lat,lon,capacity\n",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).removeprefix(f"{REMOTE}/")
        requested.append(name)
        return httpx.Response(200, content=files[name])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = dataset.local_copy(REMOTE, cache_dir=tmp_path, client=client)
        second = dataset.local_copy(REMOTE, cache_dir=tmp_path, client=client)
    assert first == second == tmp_path
    day_file = tmp_path / "daily" / "2026-10-05.parquet"
    assert day_file.read_bytes() == files["daily/2026-10-05.parquet"]
    assert requested.count("daily/2026-10-05.parquet") == 1
    assert requested.count("daily/index.csv") == 2


def test_local_copy_keeps_a_local_source(tmp_path: Path) -> None:
    assert dataset.local_copy(tmp_path, cache_dir=tmp_path / "cache") == tmp_path
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_db.py tests/unit/test_dataset.py -q`
Expected: FAIL, `velib.db` does not exist and `dataset` has no `local_copy`.

- [ ] **Step 4: Write `local_copy` and `velib.db`**

Add to `src/velib/dataset.py`, after `available_days`:

```python
def local_copy(
    source: str | Path, *, cache_dir: Path = CACHE_DIR, client: httpx.Client | None = None
) -> Path:
    """Returns a local directory with the layout of velib-data, for tools that read files.

    A local source is returned as is. For a remote one, the station file and every day of the
    index that is not cached yet are downloaded into cache_dir first.

    Args:
        source: Base URL of velib-data, or a local directory with the same layout.
        cache_dir: Local cache directory for remote sources.
        client: HTTP client to reuse. A new one is created when None.

    Returns:
        The directory that holds daily/ and stations/.
    """
    if isinstance(source, Path):
        return source
    http = client or gbfs.make_client()
    try:
        for day in available_days(source=source, cache_dir=cache_dir, client=http):
            _resolve(f"daily/{day.isoformat()}.parquet", source, cache_dir, http, refresh=False)
        _resolve(STATIONS_FILE, source, cache_dir, http, refresh=True)
    finally:
        if client is None:
            http.close()
    return cache_dir
```

<!-- file: src/velib/db.py -->
```python
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
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_db.py tests/unit/test_dataset.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/velib/dataset.py src/velib/db.py tests/unit/sources.py tests/unit/test_db.py tests/unit/test_dataset.py
git commit -m "feat: query the snapshots with SQL through DuckDB views"
```

### Task 2: Rebalancing operations

**Files:**
- Create: `src/velib/ops/__init__.py`, `src/velib/ops/regulation.py`
- Create: `tests/unit/test_regulation.py`

**Interfaces:**
- Consumes: `db.connect`, `db.query`, `tests.unit.sources.write_source`.
- Produces: `THRESHOLD = 8`, `MAX_GAP_MINUTES = 7`; `detect_operations(connection, *, threshold, max_gap_minutes) -> pl.DataFrame` with columns station_id, name, first_seen, last_seen, bikes_moved, bikes_before, bikes_after, capacity; `activity_by_hour(connection) -> pl.DataFrame` (hour, moves); `summarize_by_day(operations)` (day, operations, bikes_added, bikes_removed); `hourly_shares(operations, activity)` (hour, operations_pct, moves_pct); `top_stations(operations, *, limit) -> tuple[receivers, givers]`; `fill_before(operations) -> dict[str, Any]` (additions, removals).

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/unit/test_regulation.py -->
```python
"""Tests for the detection of rebalancing operations."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tests.unit.sources import write_source
from velib.db import connect
from velib.ops.regulation import (
    activity_by_hour,
    detect_operations,
    fill_before,
    hourly_shares,
    summarize_by_day,
    top_stations,
)

T0 = datetime(2025, 12, 9, 2, 0, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _connection(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    station_one = [2, 2, 12, 20, 21, 9]
    rows = [(_at(5 * step), 1, bikes, 0, 30 - bikes) for step, bikes in enumerate(station_one)]
    rows += [(_at(0), 2, 5, 0, 15), (_at(20), 2, 13, 0, 7)]
    return connect(write_source(tmp_path / "source", rows))


def test_detect_operations_merges_consecutive_steps_in_the_same_direction(
    tmp_path: Path,
) -> None:
    operations = detect_operations(_connection(tmp_path))
    columns = ["station_id", "name", "bikes_moved", "bikes_before", "bikes_after", "capacity"]
    assert operations.select(columns).rows() == [
        (1, "Alpha", 18, 2, 20, 30),
        (1, "Alpha", -12, 21, 9, 30),
    ]
    assert operations.get_column("first_seen").dt.strftime("%H:%M").to_list() == ["03:10", "03:25"]
    assert operations.get_column("last_seen").dt.strftime("%H:%M").to_list() == ["03:15", "03:25"]


def test_detect_operations_follows_the_threshold(tmp_path: Path) -> None:
    operations = detect_operations(_connection(tmp_path), threshold=10)
    assert operations.get_column("bikes_moved").to_list() == [10, -12]


def test_summaries_count_operations_per_day_hour_and_station(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    operations = detect_operations(connection)
    assert summarize_by_day(operations).rows() == [(date(2025, 12, 9), 2, 18, 12)]
    shares = hourly_shares(operations, activity_by_hour(connection))
    assert shares.filter(pl.col("hour") == 3).row(0) == (3, 100.0, 100.0)
    receivers, givers = top_stations(operations)
    assert receivers.select("station_id", "bikes_added").row(0) == (1, 18)
    assert givers.select("station_id", "bikes_removed").row(0) == (1, 12)
    fills = fill_before(operations)
    assert fills["additions"] == pytest.approx(2 / 30)
    assert fills["removals"] == pytest.approx(21 / 30)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_regulation.py -q`
Expected: FAIL, `velib.ops` does not exist.

- [ ] **Step 3: Write the module**

<!-- file: src/velib/ops/__init__.py -->
```python
"""Operator activity seen in the snapshots: rebalancing trucks and out-of-service bikes."""
```

<!-- file: src/velib/ops/regulation.py -->
```python
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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_regulation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/velib/ops tests/unit/test_regulation.py
git commit -m "feat: detect rebalancing operations in the snapshots"
```

### Task 3: Immobile bikes and out-of-service docks

**Files:**
- Create: `src/velib/ops/breakdowns.py`
- Create: `tests/unit/test_breakdowns.py`

**Interfaces:**
- Consumes: `db.connect`, `db.query`, `write_source`.
- Produces: `MIN_SNAPSHOTS = 200`, `MIN_DEPARTURES = 10`, `MIN_DAYS = 3`; `immobile_bikes(connection, *, min_snapshots, min_departures, min_days) -> pl.DataFrame` (station_id, name, kind, first_day, last_day, days, bikes); `immobile_per_day(immobilisations) -> pl.DataFrame` (day, kind, bikes, stations); `has_real_docks(connection) -> bool`; `unavailable_docks(connection) -> pl.DataFrame` (station_id, name, unavailable, share_of_time).

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/unit/test_breakdowns.py -->
```python
"""Tests for the immobile bikes and the out-of-service docks."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl

from tests.unit.sources import STATIONS, Row, write_source
from velib.db import connect
from velib.ops.breakdowns import (
    has_real_docks,
    immobile_bikes,
    immobile_per_day,
    unavailable_docks,
)

DAYS = [9, 10, 11]
HOURS = [8, 12, 18]
FOUR_STATIONS = pl.concat(
    [
        STATIONS,
        pl.DataFrame(
            {
                "station_id": [3, 4],
                "station_code": ["00003", "00004"],
                "name": ["Charlie", "Delta"],
                "lat": [48.87, 48.88],
                "lon": [2.37, 2.38],
                "capacity": [30, 20],
            }
        ),
    ]
)


def _immobile_connection(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    rows: list[Row] = []
    for day in DAYS:
        station_one_mechanical = [0, 3, 0] if day == 11 else [1, 3, 1]
        for index, hour in enumerate(HOURS):
            moment = datetime(2025, 12, day, hour, tzinfo=UTC)
            rows.append((moment, 1, station_one_mechanical[index], [1, 3, 1][index], 5))
            rows.append((moment, 2, [2, 4, 2][index], [0, 1, 0][index], 5))
            rows.append((moment, 3, [5, 7, 5][index], 0, 5))
            rows.append((moment, 4, 0, [3, 1, 3][index], 5))
    return connect(write_source(tmp_path / "source", rows, stations=FOUR_STATIONS))


def test_immobile_bikes_needs_consecutive_days_stuck_on_a_small_floor(tmp_path: Path) -> None:
    immobilisations = immobile_bikes(
        _immobile_connection(tmp_path),
        min_snapshots=3,
        min_departures=2,
        min_days=3,
        min_snapshots_at_floor=2,
    )
    first, last = date(2025, 12, 9), date(2025, 12, 11)
    assert immobilisations.rows() == [
        (2, "Bravo", "mechanical", first, last, 3, 2),
        (1, "Alpha", "ebike", first, last, 3, 1),
    ]
    per_day = immobile_per_day(immobilisations)
    assert per_day.head(2).rows() == [(first, "ebike", 1, 1), (first, "mechanical", 2, 1)]


def test_unavailable_docks_clips_at_zero_and_needs_real_docks(tmp_path: Path) -> None:
    moments = [datetime(2025, 12, 9, 8, tzinfo=UTC), datetime(2025, 12, 9, 9, tzinfo=UTC)]
    rows: list[Row] = [
        (moments[0], 1, 10, 5, 12),
        (moments[1], 1, 10, 5, 15),
        (moments[0], 2, 15, 5, 3),
    ]
    live = connect(write_source(tmp_path / "live", rows))
    assert has_real_docks(live)
    assert unavailable_docks(live).rows() == [(1, "Alpha", 1.5, 0.5), (2, "Bravo", 0.0, 0.0)]
    archive = connect(write_source(tmp_path / "archive", rows, real_docks=False))
    assert not has_real_docks(archive)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_breakdowns.py -q`
Expected: FAIL, `velib.ops.breakdowns` does not exist.

- [ ] **Step 3: Write the module**

<!-- file: src/velib/ops/breakdowns.py -->
```python
"""Out-of-service bikes and docks: bikes that never leave a station, docks that cannot be used."""

from __future__ import annotations

import duckdb
import polars as pl

from velib.db import query

MIN_SNAPSHOTS = 200
MIN_DEPARTURES = 10
MAX_FLOOR = 2
MIN_SNAPSHOTS_AT_FLOOR = 12
MIN_DAYS = 3

IMMOBILE_SQL = """
WITH typed AS (
    SELECT
        station_id,
        CAST(local_time AS DATE) AS day,
        'mechanical' AS kind,
        mechanical AS bikes,
        greatest(-d_mechanical, 0) AS departures
    FROM steps
    UNION ALL
    SELECT
        station_id,
        CAST(local_time AS DATE) AS day,
        'ebike' AS kind,
        ebike AS bikes,
        greatest(-d_ebike, 0) AS departures
    FROM steps
),
floors AS (
    SELECT *, min(bikes) OVER (PARTITION BY station_id, kind, day) AS floor
    FROM typed
),
days AS (
    SELECT
        station_id,
        kind,
        day,
        count(*) AS snapshots,
        min(floor) AS floor,
        sum(departures) AS departures,
        count(*) FILTER (WHERE bikes = floor) AS snapshots_at_floor
    FROM floors
    GROUP BY station_id, kind, day
),
flagged AS (
    SELECT
        *,
        day - CAST(row_number() OVER (PARTITION BY station_id, kind ORDER BY day) AS INTEGER)
            AS run
    FROM days
    WHERE snapshots >= $min_snapshots
        AND departures >= $min_departures
        AND floor BETWEEN 1 AND $max_floor
        AND snapshots_at_floor >= $min_snapshots_at_floor
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
    max_floor: int = MAX_FLOOR,
    min_snapshots_at_floor: int = MIN_SNAPSHOTS_AT_FLOOR,
    min_days: int = MIN_DAYS,
) -> pl.DataFrame:
    """Finds bikes that stay in a station for days while bikes of their type keep leaving it.

    A Paris day counts when a station has at least min_snapshots snapshots and min_departures
    departures of the type, and keeps falling back to the same few bikes of that type: its
    floor, the smallest number of the day, is between 1 and max_floor and lasts at least
    min_snapshots_at_floor snapshots. Larger floors that the station only touches are a
    surplus, not stuck bikes. At least min_days consecutive days make an immobilisation of as
    many bikes as the smallest floor: probably broken bikes or, for electric bikes,
    discharged ones.

    Args:
        connection: Connection from velib.db.connect.
        min_snapshots: Snapshots that make a full day.
        min_departures: Departures of the type that make an active day.
        max_floor: Largest floor that can be stuck bikes.
        min_snapshots_at_floor: Snapshots at the floor that make it a stuck floor; 12 is an
            hour of 5-minute snapshots.
        min_days: Shortest immobilisation, in consecutive days.

    Returns:
        One row per immobilisation: station_id, name, kind (mechanical or ebike), first_day,
        last_day, days and bikes, longest first.
    """
    params = {
        "min_snapshots": min_snapshots,
        "min_departures": min_departures,
        "max_floor": max_floor,
        "min_snapshots_at_floor": min_snapshots_at_floor,
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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_breakdowns.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/velib/ops/breakdowns.py tests/unit/test_breakdowns.py
git commit -m "feat: find immobile bikes and out-of-service docks"
```

### Task 4: Report, map and command

**Files:**
- Create: `src/velib/ops/report.py`, `src/velib/ops/map.html`, `src/velib/ops/cli.py`
- Create: `tests/unit/test_ops_report.py`
- Modify: `pyproject.toml` (script `velib-ops`)

**Interfaces:**
- Consumes: everything from Tasks 1 to 3; `velib.replay.MAPLIBRE_URL`, `velib.replay.STYLE_URL`.
- Produces: `Findings` (frozen dataclass); `analyse(connection) -> Findings`; `render_markdown(findings, label) -> str`; `map_payload(findings) -> dict[str, Any]`; `render_map_html(payload, title) -> str`; `cli.main(argv) -> int` writing `report.md` and `map.html`.

- [ ] **Step 1: Add the command**

Add to `[project.scripts]`:

```toml
velib-ops = "velib.ops.cli:main"
```

- [ ] **Step 2: Write the failing tests**

<!-- file: tests/unit/test_ops_report.py -->
```python
"""Tests for the report and the map of the operator analyses."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.unit.sources import write_source
from velib.db import connect
from velib.ops.cli import main
from velib.ops.report import Findings, analyse, map_payload, render_map_html, render_markdown

T0 = datetime(2025, 12, 9, 2, 0, tzinfo=UTC)


def _source(tmp_path: Path, *, real_docks: bool = True) -> Path:
    bikes = [2, 2, 12, 20, 21, 9]
    rows = [
        (T0 + timedelta(minutes=5 * step), 1, count, 0, 25 - count)
        for step, count in enumerate(bikes)
    ]
    rows.append((T0, 2, 5, 0, 15))
    return write_source(tmp_path / "source", rows, real_docks=real_docks)


def _findings(tmp_path: Path, *, real_docks: bool = True) -> Findings:
    return analyse(connect(_source(tmp_path, real_docks=real_docks)))


def _embedded(page: str) -> Any:
    match = re.search(r'<script type="application/json" id="ops-data">(.*?)</script>', page, re.S)
    assert match is not None
    return json.loads(match[1])


def test_render_markdown_reports_operations_and_docks(tmp_path: Path) -> None:
    report = render_markdown(_findings(tmp_path), "tiny")
    assert report.startswith("# Rebalancing and out-of-service bikes on tiny\n")
    assert "| 2025-12-09 | 2 | 18 | 12 |" in report
    assert "| Alpha | 2 | 18 |" in report
    assert "Operations with a threshold of 8, 10 and 12 bikes: 2, 2 and 1." in report
    assert "| Alpha | 5.0 | 100% |" in report


def test_render_markdown_explains_why_archives_have_no_docks(tmp_path: Path) -> None:
    report = render_markdown(_findings(tmp_path, real_docks=False), "archive")
    assert "estimates the free docks from the capacity" in report


def test_map_payload_sums_each_station_and_embeds_safely(tmp_path: Path) -> None:
    payload = map_payload(_findings(tmp_path))
    alpha = next(station for station in payload["stations"] if station["name"] == "Alpha")
    assert (alpha["operations"], alpha["added"], alpha["removed"]) == (2, 18, 12)
    assert alpha["unavailable"] == 5.0
    assert payload["totals"]["operations"] == 2
    page = render_map_html(payload | {"stations": [alpha | {"name": "</script>x"}]}, "<carte>")
    assert "<title>&lt;carte&gt;</title>" in page
    assert not re.search(r"__[A-Z]+__", page)
    assert _embedded(page)["stations"][0]["name"] == "</script>x"


def test_velib_ops_report_writes_the_report_and_the_map(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["report", "--source", str(_source(tmp_path)), "--out", str(out)]) == 0
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# Rebalancing")
    assert "ops-data" in (out / "map.html").read_text(encoding="utf-8")
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_ops_report.py -q`
Expected: FAIL, `velib.ops.report` does not exist.

- [ ] **Step 4: Write the report, the map and the command**

<!-- file: src/velib/ops/report.py -->
```python
"""Report and map of the operator analyses: rebalancing, immobile bikes, out-of-service docks."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Any

import duckdb
import polars as pl

from velib.db import query
from velib.ops.breakdowns import (
    MAX_FLOOR,
    MIN_DAYS,
    MIN_DEPARTURES,
    MIN_SNAPSHOTS,
    MIN_SNAPSHOTS_AT_FLOOR,
    has_real_docks,
    immobile_bikes,
    immobile_per_day,
    unavailable_docks,
)
from velib.ops.regulation import (
    MAX_GAP_MINUTES,
    THRESHOLD,
    activity_by_hour,
    detect_operations,
    fill_before,
    hourly_shares,
    summarize_by_day,
    top_stations,
)
from velib.replay import MAPLIBRE_URL, STYLE_URL

SENSITIVITY = (8, 10, 12)
KINDS = {"ebike": "Electric", "mechanical": "Mechanical"}


@dataclass(frozen=True)
class Findings:
    """Results of the operator analyses on one source."""

    first: datetime
    last: datetime
    stations: pl.DataFrame
    operations: pl.DataFrame
    sensitivity: dict[int, int]
    activity: pl.DataFrame
    immobile: pl.DataFrame
    unavailable: pl.DataFrame | None


def analyse(connection: duckdb.DuckDBPyConnection) -> Findings:
    """Runs the rebalancing and breakdown analyses on the views of a connection."""
    first, last = query(connection, "SELECT min(local_time), max(local_time) FROM steps").row(0)
    return Findings(
        first=first,
        last=last,
        stations=query(connection, "SELECT * FROM stations"),
        operations=detect_operations(connection),
        sensitivity={
            threshold: detect_operations(connection, threshold=threshold).height
            for threshold in SENSITIVITY
        },
        activity=activity_by_hour(connection),
        immobile=immobile_bikes(connection),
        unavailable=unavailable_docks(connection) if has_real_docks(connection) else None,
    )


def render_markdown(findings: Findings, label: str) -> str:
    """Writes the report of the analyses as Markdown."""
    operations = findings.operations
    lines = [
        f"# Rebalancing and out-of-service bikes on {label}",
        "",
        f"Snapshots from {findings.first:%Y-%m-%d %H:%M} to {findings.last:%Y-%m-%d %H:%M}, "
        f"Paris time, {findings.stations.height} stations.",
        "",
        "## Rebalancing operations",
        "",
        f"An operation is a change of at least {THRESHOLD} bikes in a station between two "
        f"snapshots at most {MAX_GAP_MINUTES} minutes apart. Consecutive steps in the same "
        "direction count as one operation.",
        "",
        _sensitivity_sentence(findings.sensitivity),
        "",
        *_table(
            ["Day", "Operations", "Bikes added", "Bikes removed"],
            summarize_by_day(operations).rows(),
        ),
    ]
    fills = fill_before(operations)
    if fills["additions"] is not None and fills["removals"] is not None:
        lines += [
            "",
            f"Just before an operation, stations that receive bikes are "
            f"{fills['additions']:.0%} full on average, and stations that lose bikes are "
            f"{fills['removals']:.0%} full.",
        ]
    shares = hourly_shares(operations, findings.activity)
    receivers, givers = top_stations(operations)
    lines += [
        "",
        "### Operations per hour, compared with all bike moves",
        "",
        *_table(
            ["Hour", "Share of operations", "Share of all bike moves"],
            [
                (f"{hour:02d}:00", f"{ops:.1f}%", f"{moves:.1f}%")
                for hour, ops, moves in shares.rows()
            ],
        ),
        "",
        "### Stations that receive the most bikes",
        "",
        *_table(
            ["Station", "Operations", "Bikes added"],
            receivers.select("name", "operations", "bikes_added").rows(),
        ),
        "",
        "### Stations that lose the most bikes",
        "",
        *_table(
            ["Station", "Operations", "Bikes removed"],
            givers.select("name", "operations", "bikes_removed").rows(),
        ),
        "",
        *_immobile_section(findings.immobile),
        "",
        *_docks_section(findings.unavailable),
    ]
    return "\n".join(lines) + "\n"


def map_payload(findings: Findings) -> dict[str, Any]:
    """Figures of each located station for the map, and the totals of the summary line."""
    operations = findings.operations.group_by("station_id").agg(
        pl.len().alias("operations"),
        pl.col("bikes_moved").clip(lower_bound=0).sum().alias("added"),
        (-pl.col("bikes_moved").clip(upper_bound=0)).sum().alias("removed"),
    )
    immobile = findings.immobile.group_by("station_id").agg(
        *[
            pl.col(column).filter(pl.col("kind") == kind).max().alias(f"{kind}_{column}")
            for kind in KINDS
            for column in ("days", "bikes")
        ]
    )
    unavailable = (
        findings.unavailable.select("station_id", pl.col("unavailable").round(2))
        if findings.unavailable is not None
        else findings.stations.select("station_id", pl.lit(None, pl.Float64).alias("unavailable"))
    )
    counts = ["operations", "added", "removed"] + [
        f"{kind}_{column}" for kind in KINDS for column in ("days", "bikes")
    ]
    table = (
        findings.stations.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null())
        .select("station_id", "name", "lat", "lon")
        .join(operations, on="station_id", how="left")
        .join(immobile, on="station_id", how="left")
        .join(unavailable, on="station_id", how="left")
        .with_columns(pl.col(counts).fill_null(0))
        .sort("station_id")
    )
    runs = findings.immobile.group_by("kind").len()
    return {
        "has_unavailable": findings.unavailable is not None,
        "totals": {
            "operations": findings.operations.height,
            "added": table.select(pl.col("added").sum()).item(),
            "removed": table.select(pl.col("removed").sum()).item(),
            "ebike_runs": _count(runs, "ebike"),
            "mechanical_runs": _count(runs, "mechanical"),
            "unavailable": (
                round(findings.unavailable.select(pl.col("unavailable").sum()).item(), 1)
                if findings.unavailable is not None
                else None
            ),
        },
        "stations": table.drop("station_id").to_dicts(),
    }


def render_map_html(payload: dict[str, Any], title: str) -> str:
    """Builds the standalone map page, with the data embedded as JSON."""
    template = files("velib.ops").joinpath("map.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        template.replace("__TITLE__", html.escape(title))
        .replace("__MAPLIBRE__", MAPLIBRE_URL)
        .replace("__STYLE__", STYLE_URL)
        .replace("__DATA__", data)
    )


def _sensitivity_sentence(sensitivity: dict[int, int]) -> str:
    thresholds = list(sensitivity)
    counts = [str(sensitivity[threshold]) for threshold in thresholds]
    return (
        f"Operations with a threshold of {', '.join(map(str, thresholds[:-1]))} and "
        f"{thresholds[-1]} bikes: {', '.join(counts[:-1])} and {counts[-1]}."
    )


def _immobile_section(immobile: pl.DataFrame) -> list[str]:
    per_day = immobile_per_day(immobile)
    rows = []
    for kind, label in KINDS.items():
        runs = immobile.filter(pl.col("kind") == kind)
        daily = per_day.filter(pl.col("kind") == kind).select(pl.col("bikes").mean()).item()
        rows.append(
            (
                label,
                runs.height,
                runs.get_column("station_id").n_unique(),
                f"{daily or 0:.0f}",
                runs.get_column("days").max() or 0,
            )
        )
    longest = immobile.head(10).with_columns(pl.col("kind").replace_strict(KINDS))
    return [
        "## Immobile bikes",
        "",
        f"A Paris day counts when a station has at least {MIN_SNAPSHOTS} snapshots and "
        f"{MIN_DEPARTURES} departures of a bike type, and keeps falling back to the same 1 to "
        f"{MAX_FLOOR} bikes of that type for at least {MIN_SNAPSHOTS_AT_FLOOR * 5} minutes in "
        f"total. At least {MIN_DAYS} consecutive such days make an immobilisation: probably "
        "broken bikes or, for electric bikes, discharged ones. Larger floors that a station "
        "only touches are a surplus of bikes, not stuck bikes.",
        "",
        *_table(
            ["Type", "Immobilisations", "Stations", "Immobile bikes per day", "Longest, in days"],
            rows,
        ),
        "",
        "### Longest immobilisations",
        "",
        *_table(
            ["Station", "Type", "From", "To", "Days", "Bikes"],
            longest.select("name", "kind", "first_day", "last_day", "days", "bikes").rows(),
        ),
    ]


def _docks_section(unavailable: pl.DataFrame | None) -> list[str]:
    lines = ["## Out-of-service docks", ""]
    if unavailable is None:
        return [
            *lines,
            "This source estimates the free docks from the capacity, so out-of-service docks "
            "cannot be measured. velib-data has the real free docks.",
        ]
    total, affected = unavailable.select(
        pl.col("unavailable").sum(), (pl.col("share_of_time") > 0).mean()
    ).row(0)
    return [
        *lines,
        "The feed counts neither broken docks nor disabled bikes: capacity minus bikes minus "
        "free docks estimates them.",
        "",
        f"On average, {total:.0f} docks are out of service at a time. {affected or 0:.0%} of the "
        "stations have at least one at some point.",
        "",
        *_table(
            ["Station", "Out-of-service docks on average", "Share of the time"],
            [
                (name, f"{mean:.1f}", f"{share:.0%}")
                for name, mean, share in unavailable.head(10)
                .select("name", "unavailable", "share_of_time")
                .rows()
            ],
        ),
    ]


def _table(header: list[str], rows: list[tuple[Any, ...]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows),
    ]


def _count(runs: pl.DataFrame, kind: str) -> int:
    match = runs.filter(pl.col("kind") == kind)
    return int(match.get_column("len").item()) if match.height else 0
```

<!-- file: src/velib/ops/cli.py -->
```python
"""Command line of the operator analyses: velib-ops report."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from velib import DATA_DIR
from velib.archives import resolve_source
from velib.db import connect
from velib.ops.report import analyse, map_payload, render_map_html, render_markdown

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-ops command line.

    Args:
        argv: Arguments, sys.argv[1:] when None.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(
        description="Find rebalancing operations and out-of-service bikes in the snapshots."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="Write the report and the map of a source.")
    report.add_argument(
        "--source",
        default="velib-data",
        help="velib-data, an imported archive such as kaggle, or a directory.",
    )
    report.add_argument(
        "--out", type=Path, default=None, help="Report directory, data/ops/<source> by default."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    label = Path(args.source).name
    out: Path = args.out or DATA_DIR / "ops" / label
    findings = analyse(connect(resolve_source(args.source)))
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render_markdown(findings, label), encoding="utf-8")
    title = f"Régulation et pannes ({label})"
    (out / "map.html").write_text(render_map_html(map_payload(findings), title), encoding="utf-8")
    logger.info("wrote %s and %s", out / "report.md", out / "map.html")
    return 0
```

<!-- file: src/velib/ops/map.html -->
````html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="__MAPLIBRE__maplibre-gl.css">
<style>
  :root { color-scheme: light; font-family: system-ui, sans-serif; }
  body { margin: 0; background: #fafafa; color: #222; }
  header { padding: 0.75rem 1rem 0.25rem; }
  h1 { margin: 0 0 0.25rem; font-size: 1.1rem; }
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; padding: 0.5rem 1rem; }
  .modes { display: inline-flex; margin: 0; padding: 0; border: 1px solid #888; border-radius: 4px; overflow: hidden; }
  .modes label { padding: 0.35rem 0.7rem; background: #fff; cursor: pointer; }
  .modes label + label { border-left: 1px solid #888; }
  .modes input { position: absolute; opacity: 0; pointer-events: none; }
  .modes label:has(input:checked) { background: #3b6ea5; color: #fff; }
  .modes label:has(input:focus-visible) { outline: 2px solid #1d3f66; outline-offset: -3px; }
  #summary { color: #555; font-variant-numeric: tabular-nums; }
  .wrapper { position: relative; }
  #map { height: calc(100vh - 8.5rem); min-height: 420px; }
  .legend { position: absolute; left: 0.75rem; bottom: 1.75rem; max-width: 17rem; padding: 0.5rem 0.75rem; border-radius: 4px; background: rgb(255 255 255 / 96%); box-shadow: 0 1px 4px rgb(0 0 0 / 20%); font-size: 0.75rem; line-height: 1.4; }
  .notice { position: absolute; top: 1rem; left: 1rem; right: 4rem; z-index: 3; padding: 0.75rem 1rem; border: 1px solid #e0c36b; border-radius: 4px; background: #fff3cd; line-height: 1.4; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div>Interventions de l’opérateur détectées dans les relevés, vélos qui ne partent jamais et places hors service. Cliquez sur une station pour voir son détail.</div>
</header>
<div class="controls">
  <fieldset class="modes" aria-label="Affichage">
    <label><input type="radio" name="mode" value="regulation" checked>Régulation</label>
    <label><input type="radio" name="mode" value="immobile">Vélos immobilisés</label>
    <label id="unavailable-mode"><input type="radio" name="mode" value="unavailable">Places hors service</label>
  </fieldset>
  <span id="summary"></span>
</div>
<div class="wrapper">
  <div id="map"></div>
  <div class="legend">
    <strong id="legend-title"></strong>
    <div id="legend-body"></div>
  </div>
  <div id="notice" class="notice" hidden>Ce navigateur refuse de lancer la carte depuis un fichier local. Ouvrez la page dans Safari, ou servez le dossier avec <code>python3 -m http.server</code>.</div>
</div>
<script type="application/json" id="ops-data">__DATA__</script>
<script type="module">
import { Map as MapLibreMap, NavigationControl, Popup } from "__MAPLIBRE__maplibre-gl.mjs";

const data = JSON.parse(document.getElementById("ops-data").textContent);
const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (text) => String(text).replace(/[&<>"']/g, (char) => ESCAPES[char]);
const count = (value) => value.toLocaleString("fr-FR");
const plural = (value, word) => `${count(value)} ${word}${value >= 2 ? "s" : ""}`;
const agreed = (value, noun, adjective) => `${plural(value, noun)} ${adjective}${value >= 2 ? "s" : ""}`;
const moved = ["+", ["get", "added"], ["get", "removed"]];
const longest = ["max", ["get", "ebike_days"], ["get", "mechanical_days"]];
const unavailable = ["coalesce", ["get", "unavailable"], 0];
const totals = data.totals;

const MODES = {
  regulation: {
    title: "Vélos déplacés par l’opérateur",
    legend: ["bleu : la station reçoit plus de vélos qu’elle n’en perd", "orange : elle en perd plus qu’elle n’en reçoit", "taille : vélos déplacés au total"],
    summary: `${plural(totals.operations, "intervention")} · ${agreed(totals.added, "vélo", "ajouté")} · ${agreed(totals.removed, "vélo", "retiré")}`,
    filter: [">", moved, 0],
    radius: ["min", 26, ["+", 2, ["*", 1.2, ["sqrt", moved]]]],
    color: ["interpolate", ["linear"], ["/", ["-", ["get", "added"], ["get", "removed"]], ["max", 1, moved]], -1, "#e66101", 0, "#f7f7f7", 1, "#2c7bb6"],
  },
  immobile: {
    title: "Vélos immobilisés",
    legend: ["violet : au moins un électrique immobilisé", "marron : des mécaniques seulement", "taille : durée la plus longue, en jours"],
    summary: `${plural(totals.ebike_runs, "immobilisation")} d’électriques · ${plural(totals.mechanical_runs, "immobilisation")} de mécaniques`,
    filter: [">", longest, 0],
    radius: ["+", 2, ["*", 0.6, longest]],
    color: ["case", [">", ["get", "ebike_days"], 0], "#7b3294", "#a6611a"],
  },
  unavailable: {
    title: "Places hors service",
    legend: ["taille : places hors service en moyenne", "estimées par capacité − vélos − bornes libres"],
    summary: totals.unavailable === null ? "" : `${plural(Math.round(totals.unavailable), "place")} hors service en moyenne`,
    filter: [">", unavailable, 0],
    radius: ["min", 26, ["+", 2, ["*", 4, ["sqrt", unavailable]]]],
    color: "#d7191c",
  },
};

if (!data.has_unavailable) {
  document.getElementById("unavailable-mode").hidden = true;
}

const map = new MapLibreMap({
  container: "map",
  style: "__STYLE__",
  center: [2.3488, 48.8534],
  zoom: 11.3,
  attributionControl: { compact: true },
});
map.addControl(new NavigationControl({ showCompass: false }), "top-right");
map.on("error", (event) => {
  console.error(event.error);
  if (location.protocol === "file:" && String(event.error?.message).includes("Worker")) {
    document.getElementById("notice").hidden = false;
  }
});

function setMode(mode) {
  const config = MODES[mode];
  document.getElementById("legend-title").textContent = config.title;
  document.getElementById("legend-body").replaceChildren(
    ...config.legend.map((text) => Object.assign(document.createElement("div"), { textContent: text })),
  );
  document.getElementById("summary").textContent = config.summary;
  if (!map.getLayer("stations")) {
    return;
  }
  map.setFilter("stations", config.filter);
  map.setPaintProperty("stations", "circle-radius", config.radius);
  map.setPaintProperty("stations", "circle-color", config.color);
}

function describe(station) {
  const lines = [`<strong>${escapeHtml(station.name)}</strong>`];
  if (station.operations > 0) {
    lines.push(`<div>${plural(station.operations, "intervention")} : +${count(station.added)} / −${count(station.removed)} vélos</div>`);
  }
  if (station.ebike_days > 0) {
    lines.push(`<div>${plural(station.ebike_bikes, "électrique")} immobilisé${station.ebike_bikes >= 2 ? "s" : ""} pendant ${station.ebike_days} jours</div>`);
  }
  if (station.mechanical_days > 0) {
    lines.push(`<div>${plural(station.mechanical_bikes, "mécanique")} immobilisé${station.mechanical_bikes >= 2 ? "s" : ""} pendant ${station.mechanical_days} jours</div>`);
  }
  if (data.has_unavailable && station.unavailable > 0) {
    lines.push(`<div>${plural(station.unavailable, "place")} hors service en moyenne</div>`);
  }
  return lines.join("");
}

map.on("load", () => {
  map.addSource("stations", {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: data.stations.map((station, index) => ({
        type: "Feature",
        id: index,
        geometry: { type: "Point", coordinates: [station.lon, station.lat] },
        properties: station,
      })),
    },
  });
  map.addLayer({
    id: "stations",
    type: "circle",
    source: "stations",
    paint: { "circle-stroke-color": "#555555", "circle-stroke-width": 0.6, "circle-opacity": 0.85 },
  });
  map.on("click", "stations", (event) => {
    const station = data.stations[event.features[0].id];
    new Popup({ maxWidth: "280px" }).setLngLat([station.lon, station.lat]).setHTML(describe(station)).addTo(map);
  });
  map.on("mouseenter", "stations", () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "stations", () => {
    map.getCanvas().style.cursor = "";
  });
  setMode(document.querySelector('input[name="mode"]:checked').value);
});

for (const input of document.querySelectorAll('input[name="mode"]')) {
  input.addEventListener("change", () => setMode(input.value));
}
setMode(document.querySelector('input[name="mode"]:checked').value);
</script>
</body>
</html>
````

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_ops_report.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/velib/ops tests/unit/test_ops_report.py
git commit -m "feat: report and map the rebalancing and the breakdowns"
```

### Task 5: Run on Kaggle, check the map, publish

**Files:**
- Create: `docs/operations.md`
- Modify: `mkdocs.yml` (nav), `docs/data.md` (commands), the spec (results)

- [ ] **Step 1: Run the report on Kaggle and on velib-data**

Run: `uv run --group db velib-ops report --source kaggle` and `uv run --group db velib-ops report --source velib-data`
Expected: `data/ops/kaggle/report.md` and `map.html`, the same for velib-data, with a night share of operations well above the night share of all moves.

- [ ] **Step 2: Check the map in WebKit**

Install Playwright WebKit in the scratchpad, open `data/ops/kaggle/map.html`, switch between the views, click a station, check there is no console error, then delete the tooling.

- [ ] **Step 3: Write the site page and the results**

Write `docs/operations.md` (English) with the Kaggle results and the out-of-service docks of velib-data, add it to the `nav` of `mkdocs.yml`, add the `velib-db` and `velib-ops` commands to `docs/data.md`, and add a results section to the spec.

- [ ] **Step 4: Check, commit and push**

Run: `uv run pytest`, `uv run pre-commit run --all-files`, `uv run mkdocs build --strict`, the identity scan, then commit `docs: publish the rebalancing and breakdown findings` and push.
