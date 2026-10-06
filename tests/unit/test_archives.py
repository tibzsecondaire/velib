"""Tests for the import of public archives."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from velib import archives

DAILY_SCHEMA = pl.Schema(
    {
        "fetched_at": pl.Datetime("us", "UTC"),
        "feed_updated_at": pl.Datetime("us", "UTC"),
        "station_id": pl.Int64(),
        "mechanical": pl.Int16(),
        "ebike": pl.Int16(),
        "docks": pl.Int16(),
        "is_installed": pl.Boolean(),
        "is_renting": pl.Boolean(),
        "is_returning": pl.Boolean(),
        "last_reported": pl.Datetime("us", "UTC"),
    }
)

CURRENT = pl.DataFrame(
    {
        "station_id": [6245, 213688169],
        "station_code": ["1116", "16107"],
        "name": ["Ventadour - Opéra", "Benjamin Godard - Victor Hugo"],
        "lat": [48.866810, 48.865983],
        "lon": [2.334388, 2.275725],
        "capacity": [27, 35],
    }
)


def _kaggle_raw(last_fetch: datetime = datetime(2025, 12, 2, 23, 58, 10)) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ts_utc": [
                datetime(2025, 12, 2, 0, 0, 49),
                datetime(2025, 12, 2, 0, 5, 50),
                last_fetch,
            ],
            "tbin_utc": [
                datetime(2025, 12, 2, 0, 0),
                datetime(2025, 12, 2, 0, 5),
                datetime(2025, 12, 2, 23, 55),
            ],
            "station_id": [6245, 6245, 6293],
            "bikes": [16, 30, 7],
            "capacity": [27, 27, 28],
            "mechanical": [10, 20, 5],
            "ebike": [6, 10, 2],
            "status": ["OK", "CLOSED", "OK"],
            "lat": [48.86681, 48.86681, 48.86722],
            "lon": [2.33439, 2.33439, 2.34046],
            "name": ["Ventadour - Opéra", "Ventadour - Opéra", "Mairie du 2ème"],
            "temp_C": [6.9, 6.8, 5.0],
            "precip_mm": [0.0, 0.0, 0.2],
            "wind_mps": [4.28, 4.1, 3.0],
        },
        schema_overrides={"ts_utc": pl.Datetime("ns"), "tbin_utc": pl.Datetime("ns")},
    )


def _lovasoa_raw() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": ["2021-02-10T08:00Z", "2021-02-10T08:00Z", "2021-02-10T08:15Z"],
            "capacity": [35, 27, 35],
            "mechanical": [4, 10, 40],
            "electrical": [5, 2, 0],
            "name": [
                "Benjamin Godard - Victor Hugo",
                "Ventadour (old name)",
                "Benjamin Godard - Victor Hugo",
            ],
            "geo": ["48.86598,2.27572", "48.86685,2.33440", "48.86598,2.27572"],
            "operative": [True, False, True],
        }
    )


def test_normalize_kaggle_matches_the_daily_schema() -> None:
    frame = archives.normalize_kaggle(_kaggle_raw())
    assert frame.schema == DAILY_SCHEMA
    assert frame.get_column("fetched_at").to_list()[0] == datetime(
        2025, 12, 2, 0, 0, 49, tzinfo=UTC
    )


def test_normalize_kaggle_estimates_docks_and_reads_the_status() -> None:
    frame = archives.normalize_kaggle(_kaggle_raw())
    assert frame.get_column("docks").to_list() == [11, 0, 21]
    assert frame.get_column("is_renting").to_list() == [True, False, True]


def test_kaggle_stations_and_weather() -> None:
    raw = _kaggle_raw()
    stations = archives.kaggle_stations(raw)
    assert stations.columns == ["station_id", "station_code", "name", "lat", "lon", "capacity"]
    assert stations.get_column("station_id").to_list() == [6245, 6293]
    weather = archives.kaggle_weather(raw)
    assert weather.columns == ["time_bin", "temp_c", "precip_mm", "wind_mps"]
    assert weather.height == 3


def test_match_stations_prefers_position_then_name_then_a_stable_negative_id() -> None:
    pairs = pl.DataFrame(
        {
            "name": ["Ventadour (old name)", "Benjamin Godard - Victor Hugo", "Gone station"],
            "lat": [48.86685, 48.80000, 48.90000],
            "lon": [2.33440, 2.20000, 2.40000],
        }
    )
    matches = archives.match_stations(pairs, CURRENT)
    assert matches.get_column("match").to_list() == ["position", "name", "none"]
    ids = matches.get_column("station_id").to_list()
    assert ids[:2] == [6245, 213688169]
    assert ids[2] < 0
    assert archives.match_stations(pairs[2:], CURRENT).get_column("station_id").to_list() == [
        ids[2]
    ]


def test_normalize_lovasoa_matches_stations_and_the_daily_schema() -> None:
    frame, stations = archives.normalize_lovasoa(_lovasoa_raw(), CURRENT)
    assert frame.schema == DAILY_SCHEMA
    assert frame.get_column("station_id").to_list() == [6245, 213688169, 213688169]
    assert frame.get_column("ebike").to_list() == [2, 5, 0]
    assert frame.get_column("docks").to_list() == [15, 26, 0]
    assert frame.get_column("is_installed").to_list() == [False, True, True]
    assert stations.get_column("station_code").to_list() == ["1116", "16107"]


def test_write_archive_writes_one_file_per_utc_day(tmp_path: Path) -> None:
    raw = _kaggle_raw(last_fetch=datetime(2025, 12, 3, 0, 1, 0))
    out = tmp_path / "kaggle"
    summaries = archives.write_archive(
        archives.normalize_kaggle(raw), archives.kaggle_stations(raw), out
    )
    assert [summary.day for summary in summaries] == [date(2025, 12, 2), date(2025, 12, 3)]
    assert sorted(path.name for path in (out / "daily").iterdir()) == [
        "2025-12-02.parquet",
        "2025-12-03.parquet",
        "index.csv",
    ]
    index = (out / "daily" / "index.csv").read_text(encoding="utf-8").splitlines()
    assert index[1].startswith("2025-12-02,2,")
    header = (
        (out / "stations" / "station_information.csv").read_text(encoding="utf-8").splitlines()[0]
    )
    assert header == "station_id,station_code,name,lat,lon,capacity"


def test_import_kaggle_from_a_zip_writes_the_weather(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    _kaggle_raw().write_parquet(buffer)
    zip_path = tmp_path / "kaggle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("velib_concat.parquet", buffer.getvalue())
    exit_code = archives.main(
        ["import", "kaggle", "--zip", str(zip_path), "--out", str(tmp_path / "out")]
    )
    assert exit_code == 0
    assert (tmp_path / "out" / "daily" / "2025-12-02.parquet").is_file()
    assert pl.read_parquet(tmp_path / "out" / "weather.parquet").height == 3


def test_import_lovasoa_reads_the_csv_without_header(tmp_path: Path) -> None:
    zip_path = tmp_path / "lovasoa.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("historique_stations.csv", _lovasoa_raw().write_csv(include_header=False))
    summaries = archives.import_lovasoa(zip_path, tmp_path / "out", CURRENT)
    assert [summary.day for summary in summaries] == [date(2021, 2, 10)]
    assert pl.read_parquet(tmp_path / "out" / "daily" / "2021-02-10.parquet").height == 3
