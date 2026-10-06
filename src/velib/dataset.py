"""Load velib-data files (daily Parquet, station information) from GitHub or a local directory."""

from __future__ import annotations

from datetime import date, timedelta
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
    """Loads one compacted UTC day.

    A local source is read directly. A remote one is downloaded once into the cache.

    Args:
        day: UTC day to load.
        source: Base URL of velib-data, or a local directory with the same layout.
        cache_dir: Local cache directory for remote sources.
        client: HTTP client to reuse. A new one is created when None.

    Returns:
        One row per station and per snapshot.

    Raises:
        FileNotFoundError: The day has not been compacted.
    """
    relative_path = f"daily/{day.isoformat()}.parquet"
    return pl.read_parquet(_resolve(relative_path, source, cache_dir, client, refresh=False))


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
        cache_dir: Local cache directory for remote sources.
        client: HTTP client to reuse. A new one is created when None.
        refresh: Download a remote file again even when it is cached.

    Returns:
        One row per station.
    """
    path = _resolve(STATIONS_FILE, source, cache_dir, client, refresh=refresh)
    return pl.read_csv(path, schema_overrides={"station_code": pl.String})


def available_days(
    *,
    source: str | Path = DATA_SOURCE,
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
) -> list[date]:
    """Lists the compacted UTC days of a source, from its daily/index.csv.

    A remote index is downloaded again on every call, since it changes every night.

    Args:
        source: Base URL of velib-data, or a local directory with the same layout.
        cache_dir: Local cache directory for remote sources.
        client: HTTP client to reuse. A new one is created when None.

    Returns:
        The days, in order.
    """
    path = _resolve("daily/index.csv", source, cache_dir, client, refresh=True)
    days: list[date] = pl.read_csv(path, try_parse_dates=True).get_column("date").to_list()
    return sorted(days)


def load_period(
    *,
    source: str | Path = DATA_SOURCE,
    start: date | None = None,
    end: date | None = None,
    time_zone: str = "Europe/Paris",
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
) -> pl.DataFrame:
    """Loads the snapshots of the local days from start to end, both included.

    Args:
        source: Base URL of velib-data, or a local directory with the same layout.
        start: First local day. Defaults to the first available day.
        end: Last local day. Defaults to the last available day.
        time_zone: Time zone that defines the local days.
        cache_dir: Local cache directory for remote sources.
        client: HTTP client to reuse. A new one is created when None.

    Returns:
        The snapshots of the period, in the daily schema.

    Raises:
        FileNotFoundError: No compacted day falls in the period.
    """
    wanted = [
        day
        for day in available_days(source=source, cache_dir=cache_dir, client=client)
        if (start is None or day >= start - timedelta(days=1)) and (end is None or day <= end)
    ]
    if not wanted:
        raise FileNotFoundError(f"no compacted day between {start} and {end}")
    frame = pl.concat(
        [load_day(day, source=source, cache_dir=cache_dir, client=client) for day in wanted]
    )
    local_day = pl.col("fetched_at").dt.convert_time_zone(time_zone).dt.date()
    if start is not None:
        frame = frame.filter(local_day >= start)
    if end is not None:
        frame = frame.filter(local_day <= end)
    return frame


def _resolve(
    relative_path: str,
    source: str | Path,
    cache_dir: Path,
    client: httpx.Client | None,
    *,
    refresh: bool,
) -> Path:
    if isinstance(source, Path):
        path = source / relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    cached = cache_dir / relative_path
    if refresh or not cached.exists():
        url = f"{source.rstrip('/')}/{relative_path}"
        if client is None:
            with gbfs.make_client() as own_client:
                content = _download(own_client, url)
        else:
            content = _download(client, url)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(content)
    return cached


def _download(client: httpx.Client, url: str) -> bytes:
    response = client.get(url)
    if response.status_code == httpx.codes.NOT_FOUND:
        raise FileNotFoundError(url)
    response.raise_for_status()
    return response.content
