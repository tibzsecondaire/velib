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
