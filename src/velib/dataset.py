"""Load velib-data files (daily Parquet, station information) through a local cache."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import polars as pl

from velib import DATA_DIR, gbfs

DATA_SOURCE = "https://raw.githubusercontent.com/tibzsecondaire/velib-data/main"
CACHE_DIR = DATA_DIR / "cache"
STATIONS_FILE = "stations/station_information.csv"


def load_day(
    day: date,
    *,
    source: str | Path = DATA_SOURCE,
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
) -> pl.DataFrame:
    """Loads one compacted UTC day, downloading it once into the cache.

    Args:
        day: UTC day to load.
        source: Base URL of velib-data, or a local directory with the same layout.
        cache_dir: Local cache directory.
        client: HTTP client to reuse. A new one is created when None.

    Returns:
        One row per station and per snapshot.

    Raises:
        FileNotFoundError: The day has not been compacted.
    """
    relative_path = f"daily/{day.isoformat()}.parquet"
    cached = cache_dir / relative_path
    if not cached.exists():
        _store(cached, _fetch(relative_path, source, client))
    return pl.read_parquet(cached)


def load_stations(
    *,
    source: str | Path = DATA_SOURCE,
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> pl.DataFrame:
    """Loads the station information: code, name, coordinates and capacity.

    Args:
        source: Base URL of velib-data, or a local directory with the same layout.
        cache_dir: Local cache directory.
        client: HTTP client to reuse. A new one is created when None.
        refresh: Download again even when the file is cached.

    Returns:
        One row per station.
    """
    cached = cache_dir / STATIONS_FILE
    if refresh or not cached.exists():
        _store(cached, _fetch(STATIONS_FILE, source, client))
    return pl.read_csv(cached, schema_overrides={"station_code": pl.String})


def _fetch(relative_path: str, source: str | Path, client: httpx.Client | None) -> bytes:
    if isinstance(source, Path):
        path = source / relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path.read_bytes()
    url = f"{source.rstrip('/')}/{relative_path}"
    if client is None:
        with gbfs.make_client() as own_client:
            return _download(own_client, url)
    return _download(client, url)


def _download(client: httpx.Client, url: str) -> bytes:
    response = client.get(url)
    if response.status_code == httpx.codes.NOT_FOUND:
        raise FileNotFoundError(url)
    response.raise_for_status()
    return response.content


def _store(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
