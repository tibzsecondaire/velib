"""End-to-end test of velib-forecast evaluate on a tiny synthetic archive."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from velib.collect.compact import STATION_COLUMNS
from velib.forecast import cli

pytestmark = pytest.mark.integration

START = datetime(2025, 12, 1, 0, 0, tzinfo=UTC)


def _write_archive(root: Path) -> None:
    rows = []
    for slot in range(2 * 288):
        time = START + timedelta(minutes=5 * slot)
        for station_id in (1, 2, 3):
            bikes = round(10 + 8 * math.sin(2 * math.pi * slot / 288 + station_id))
            rows.append(
                {
                    "fetched_at": time + timedelta(seconds=20),
                    "feed_updated_at": None,
                    "station_id": station_id,
                    "mechanical": bikes,
                    "ebike": 0,
                    "docks": 25 - bikes,
                    "is_installed": True,
                    "is_renting": True,
                    "is_returning": True,
                    "last_reported": None,
                }
            )
    frame = pl.DataFrame(
        rows,
        schema_overrides={
            "fetched_at": pl.Datetime("us", "UTC"),
            "feed_updated_at": pl.Datetime("us", "UTC"),
            "last_reported": pl.Datetime("us", "UTC"),
            "mechanical": pl.Int16,
            "ebike": pl.Int16,
            "docks": pl.Int16,
        },
    )
    daily = root / "daily"
    daily.mkdir(parents=True)
    days = frame.with_columns(pl.col("fetched_at").dt.date().alias("day"))
    for key, part in days.partition_by("day", as_dict=True).items():
        part.drop("day").write_parquet(daily / f"{key[0].isoformat()}.parquet")
    stations = pl.DataFrame(
        {"station_id": [1, 2, 3], "station_code": ["", "", ""], "name": ["A", "B", "C"]}
    ).with_columns(
        pl.lit(48.85).alias("lat"), pl.lit(2.35).alias("lon"), pl.lit(25).alias("capacity")
    )
    (root / "stations").mkdir()
    stations.select(STATION_COLUMNS).write_csv(root / "stations" / "station_information.csv")
    weather = pl.DataFrame(
        {"time_bin": [START + timedelta(minutes=5 * slot) for slot in range(2 * 288)]}
    ).with_columns(
        pl.lit(8.0).alias("temp_c"), pl.lit(0.0).alias("precip_mm"), pl.lit(3.0).alias("wind_mps")
    )
    weather.with_columns(pl.col("time_bin").cast(pl.Datetime("us", "UTC"))).write_parquet(
        root / "weather.parquet"
    )


def test_evaluate_writes_metrics_and_report(tmp_path: Path) -> None:
    _write_archive(tmp_path / "archive")
    out = tmp_path / "out"
    exit_code = cli.main(
        [
            "evaluate",
            "--source",
            str(tmp_path / "archive"),
            "--test-start",
            "2025-12-02T00:00:00Z",
            "--horizons",
            "15",
            "60",
            "--max-iter",
            "20",
            "--importance-horizon",
            "60",
            "--sample",
            "200",
            "--out",
            str(out),
        ]
    )
    assert exit_code == 0
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert sorted(metrics["horizons"]) == ["15", "60"]
    scores = metrics["horizons"]["60"]["scores"]
    assert set(scores) == {"persistence", "profile", "adjusted persistence", "model"}
    assert metrics["horizons"]["60"]["importance"]
    assert "| 60 min |" in (out / "report.md").read_text(encoding="utf-8")
