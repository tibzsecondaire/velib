"""Tests for loading velib-data files."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest

from velib import dataset

DAY = date(2026, 10, 5)
REMOTE = "https://example.org/velib-data"


def _frame() -> pl.DataFrame:
    return pl.DataFrame({"station_id": [1, 2], "mechanical": [3, 4]})


def _parquet_bytes() -> bytes:
    buffer = io.BytesIO()
    _frame().write_parquet(buffer)
    return buffer.getvalue()


def test_load_day_from_a_local_source_fills_the_cache(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "daily").mkdir(parents=True)
    _frame().write_parquet(source / "daily" / "2026-10-05.parquet")
    cache = tmp_path / "cache"
    assert dataset.load_day(DAY, source=source, cache_dir=cache).equals(_frame())
    (source / "daily" / "2026-10-05.parquet").unlink()
    assert dataset.load_day(DAY, source=source, cache_dir=cache).equals(_frame())


def test_load_day_missing_from_a_local_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        dataset.load_day(DAY, source=tmp_path, cache_dir=tmp_path / "cache")


def test_load_day_downloads_from_a_url(tmp_path: Path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=_parquet_bytes())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame = dataset.load_day(DAY, source=REMOTE, cache_dir=tmp_path, client=client)
    assert frame.equals(_frame())
    assert requested == [f"{REMOTE}/daily/2026-10-05.parquet"]


def test_load_day_turns_a_404_into_file_not_found(tmp_path: Path) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client,
        pytest.raises(FileNotFoundError),
    ):
        dataset.load_day(DAY, source=REMOTE, cache_dir=tmp_path, client=client)


def test_load_stations_keeps_station_codes_as_text(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "stations").mkdir(parents=True)
    (source / "stations" / "station_information.csv").write_text(
        "station_id,station_code,name,lat,lon,capacity\n1,00001,A,48.8,2.3,20\n", encoding="utf-8"
    )
    stations = dataset.load_stations(source=source, cache_dir=tmp_path / "cache")
    assert stations.get_column("station_code").to_list() == ["00001"]
