"""Import public Vélib' archives into the velib-data layout, for analysis."""

from __future__ import annotations

import argparse
import io
import logging
import sys
import tempfile
import zipfile
import zlib
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from velib import DATA_DIR, gbfs
from velib.collect.compact import (
    INDEX_FILENAME,
    STATION_COLUMNS,
    DaySummary,
    summarize,
    update_index,
)
from velib.dataset import DATA_SOURCE, load_stations

logger = logging.getLogger(__name__)

ARCHIVES_DIR = DATA_DIR / "archives"
ARCHIVE_URLS = {
    "kaggle": "https://www.kaggle.com/api/v1/datasets/download/adrienmorel97/velib-data",
    "lovasoa": (
        "https://github.com/lovasoa/historique-velib-opendata/releases/latest/download/stations.zip"
    ),
}
KAGGLE_FILE = "velib_concat.parquet"
LOVASOA_FILE = "historique_stations.csv"
LOVASOA_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.String(),
    "capacity": pl.Int64(),
    "mechanical": pl.Int64(),
    "electrical": pl.Int64(),
    "name": pl.String(),
    "geo": pl.String(),
    "operative": pl.Boolean(),
}
MATCH_RADIUS_METERS = 100.0
EARTH_RADIUS_METERS = 6_371_000.0

_UTC = pl.Datetime("us", "UTC")


def resolve_source(value: str) -> str | Path:
    """Turns a source name into a load_day source.

    Args:
        value: "velib-data", the name of an imported archive, or a directory with the
            velib-data layout.

    Returns:
        The URL of velib-data, or the local directory.

    Raises:
        SystemExit: The source is unknown.
    """
    if value == "velib-data":
        return DATA_SOURCE
    for candidate in (ARCHIVES_DIR / value, Path(value)):
        if candidate.is_dir():
            return candidate
    raise SystemExit(
        f"unknown source {value!r}: import it first with `velib-archives import {value}`"
    )


def normalize_kaggle(raw: pl.DataFrame) -> pl.DataFrame:
    """Converts the Kaggle table to the daily schema of velib-data.

    The archive has no free docks: they are estimated as capacity minus bikes. A few snapshots
    lost the split between mechanical and electric bikes: every station shows 0 of each while
    its total is right. These snapshots are dropped.
    """
    by_type = (pl.col("mechanical") + pl.col("ebike")).sum().over("ts_utc")
    lost_types = (by_type == 0) & (pl.col("bikes").sum().over("ts_utc") > 0)
    return _daily_frame(
        raw.filter(~lost_types),
        fetched_at=pl.col("ts_utc").dt.replace_time_zone("UTC").dt.cast_time_unit("us"),
        ebike="ebike",
        docks=pl.col("capacity") - pl.col("bikes"),
        is_open=pl.col("status") == "OK",
    )


def kaggle_stations(raw: pl.DataFrame) -> pl.DataFrame:
    """Latest name, position and capacity of each station of the Kaggle table."""
    return (
        raw.sort("ts_utc")
        .group_by("station_id")
        .agg(pl.col("name", "lat", "lon", "capacity").last())
        .with_columns(pl.lit("").alias("station_code"))
        .select(STATION_COLUMNS)
        .sort("station_id")
    )


def kaggle_weather(raw: pl.DataFrame) -> pl.DataFrame:
    """City weather of each 5-minute slot of the Kaggle table."""
    return (
        raw.group_by("tbin_utc")
        .agg(pl.col("temp_C").first().alias("temp_c"), pl.col("precip_mm", "wind_mps").first())
        .select(
            pl.col("tbin_utc")
            .dt.replace_time_zone("UTC")
            .dt.cast_time_unit("us")
            .alias("time_bin"),
            "temp_c",
            "precip_mm",
            "wind_mps",
        )
        .sort("time_bin")
    )


def match_stations(
    pairs: pl.DataFrame, current: pl.DataFrame, radius_m: float = MATCH_RADIUS_METERS
) -> pl.DataFrame:
    """Gives a station_id to each name and position of an old archive.

    The nearest current station within radius_m wins, then a current station with the same
    name, then a stable negative id computed from the name.

    Args:
        pairs: Columns name, lat and lon, one row per distinct station of the archive.
        current: Current station information, with station_id, name, lat and lon.
        radius_m: Largest distance, in metres, for a match by position.

    Returns:
        The pairs, in the same order, with a station_id column and a match column worth
        "position", "name" or "none".
    """
    candidates = current.select(
        pl.col("station_id").alias("candidate_id"),
        pl.col("lat").alias("candidate_lat"),
        pl.col("lon").alias("candidate_lon"),
    )
    lat1 = pl.col("lat").radians()
    lat2 = pl.col("candidate_lat").radians()
    half_dlat = (lat2 - lat1) / 2
    half_dlon = (pl.col("candidate_lon") - pl.col("lon")).radians() / 2
    chord = half_dlat.sin().pow(2) + lat1.cos() * lat2.cos() * half_dlon.sin().pow(2)
    nearest = (
        pairs.join(candidates, how="cross")
        .with_columns((2 * EARTH_RADIUS_METERS * chord.sqrt().arcsin()).alias("distance"))
        .sort("distance")
        .group_by("name", "lat", "lon", maintain_order=True)
        .first()
        .select("name", "lat", "lon", "candidate_id", "distance")
    )
    by_name = current.group_by("name").agg(pl.col("station_id").min().alias("name_id"))
    close = pl.col("distance") <= radius_m
    named = pl.col("name_id").is_not_null()
    return (
        pairs.join(nearest, on=["name", "lat", "lon"], how="left", maintain_order="left")
        .join(by_name, on="name", how="left", maintain_order="left")
        .with_columns(
            pl.when(close)
            .then(pl.col("candidate_id"))
            .when(named)
            .then(pl.col("name_id"))
            .otherwise(pl.col("name").map_elements(_synthetic_id, return_dtype=pl.Int64))
            .alias("station_id"),
            pl.when(close)
            .then(pl.lit("position"))
            .when(named)
            .then(pl.lit("name"))
            .otherwise(pl.lit("none"))
            .alias("match"),
        )
        .select("name", "lat", "lon", "station_id", "match")
    )


def normalize_lovasoa(
    raw: pl.DataFrame, current: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Converts the lovasoa table to the daily schema and builds its station information.

    The archive has no station id and no free docks: stations are matched with
    match_stations, and free docks are estimated as capacity minus bikes. When 2 old
    stations match the same current one at the same time, the first is kept.

    Args:
        raw: The lovasoa table, with the columns of LOVASOA_SCHEMA.
        current: Current station information.

    Returns:
        The daily frame and the station information.
    """
    located = (
        raw.with_columns(
            pl.col("date")
            .str.to_datetime("%Y-%m-%dT%H:%MZ", time_unit="us", time_zone="UTC")
            .alias("fetched_at"),
            pl.col("geo")
            .str.split_exact(",", 1)
            .struct.rename_fields(["lat", "lon"])
            .alias("position"),
        )
        .unnest("position")
        .with_columns(pl.col("lat").cast(pl.Float64), pl.col("lon").cast(pl.Float64))
    )
    matches = match_stations(
        located.select("name", "lat", "lon").unique(maintain_order=True), current
    )
    rows = located.join(
        matches.select("name", "lat", "lon", "station_id"), on=["name", "lat", "lon"], how="left"
    )
    frame = _daily_frame(
        rows,
        fetched_at=pl.col("fetched_at"),
        ebike="electrical",
        docks=pl.col("capacity") - pl.col("mechanical") - pl.col("electrical"),
        is_open=pl.col("operative"),
    ).unique(subset=["station_id", "fetched_at"], keep="first", maintain_order=True)
    stations = (
        rows.sort("fetched_at")
        .group_by("station_id")
        .agg(pl.col("name", "lat", "lon", "capacity").last())
        .join(current.select("station_id", "station_code"), on="station_id", how="left")
        .with_columns(pl.col("station_code").fill_null(""))
        .select(STATION_COLUMNS)
        .sort("station_id")
    )
    return frame, stations


def write_archive(frame: pl.DataFrame, stations: pl.DataFrame, out_dir: Path) -> list[DaySummary]:
    """Writes a converted archive with the layout of velib-data.

    Args:
        frame: Daily-schema rows of the whole archive.
        stations: Station information with STATION_COLUMNS.
        out_dir: Directory of the archive, created when missing.

    Returns:
        The index line of each UTC day, in date order.
    """
    daily_dir = out_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    days = frame.with_columns(pl.col("fetched_at").dt.date().alias("day")).partition_by(
        "day", as_dict=True
    )
    summaries = []
    for key in sorted(days):
        day = key[0]
        part = days[key].drop("day").sort("station_id", "fetched_at")
        part.write_parquet(daily_dir / f"{day.isoformat()}.parquet", compression="zstd")
        summary = summarize(day, part)
        update_index(daily_dir / INDEX_FILENAME, summary)
        summaries.append(summary)
    stations_path = out_dir / "stations" / "station_information.csv"
    stations_path.parent.mkdir(parents=True, exist_ok=True)
    stations.select(STATION_COLUMNS).sort("station_id").write_csv(stations_path)
    return summaries


def download(url: str, path: Path) -> Path:
    """Downloads url to path, unless path already exists.

    Args:
        url: Address of the file.
        path: Destination file.

    Returns:
        The path of the downloaded file.
    """
    if path.exists():
        logger.info("using the existing %s", path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    logger.info("downloading %s", url)
    with gbfs.make_client() as client, client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    partial.replace(path)
    return path


def import_kaggle(zip_path: Path, out_dir: Path) -> list[DaySummary]:
    """Converts the Kaggle archive into out_dir, with its weather."""
    with zipfile.ZipFile(zip_path) as archive:
        raw = pl.read_parquet(io.BytesIO(archive.read(KAGGLE_FILE)))
    summaries = write_archive(normalize_kaggle(raw), kaggle_stations(raw), out_dir)
    kaggle_weather(raw).write_parquet(out_dir / "weather.parquet", compression="zstd")
    return summaries


def import_lovasoa(zip_path: Path, out_dir: Path, current: pl.DataFrame) -> list[DaySummary]:
    """Converts the lovasoa archive into out_dir, matching its stations with current ones."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(dir=out_dir.parent) as scratch,
        zipfile.ZipFile(zip_path) as archive,
    ):
        csv_path = Path(archive.extract(LOVASOA_FILE, scratch))
        raw = pl.read_csv(csv_path, has_header=False, schema=LOVASOA_SCHEMA)
    frame, stations = normalize_lovasoa(raw, current)
    return write_archive(frame, stations, out_dir)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-archives command line.

    Args:
        argv: Arguments without the program name. Defaults to sys.argv[1:].

    Returns:
        The process exit code.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    # httpx logs every redirect with its long signed URL; one line per download is enough.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        prog="velib-archives", description="Import public Vélib' archives for analysis."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser(
        "import", help="Download an archive and convert it to the velib-data layout."
    )
    importer.add_argument("name", choices=sorted(ARCHIVE_URLS))
    importer.add_argument("--zip", type=Path, default=None, help="Archive zip already downloaded.")
    importer.add_argument(
        "--out", type=Path, default=None, help="Output directory. Defaults to data/archives/<name>."
    )
    args = parser.parse_args(argv)
    out_dir: Path = args.out or ARCHIVES_DIR / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path: Path = args.zip or download(
        ARCHIVE_URLS[args.name], ARCHIVES_DIR / "downloads" / f"{args.name}.zip"
    )
    if args.name == "kaggle":
        summaries = import_kaggle(zip_path, out_dir)
    else:
        summaries = import_lovasoa(zip_path, out_dir, current=load_stations())
    logger.info("wrote %d days to %s", len(summaries), out_dir)
    return 0


def _daily_frame(
    rows: pl.DataFrame, *, fetched_at: pl.Expr, ebike: str, docks: pl.Expr, is_open: pl.Expr
) -> pl.DataFrame:
    return rows.select(
        fetched_at.alias("fetched_at"),
        pl.lit(None, dtype=_UTC).alias("feed_updated_at"),
        pl.col("station_id").cast(pl.Int64),
        pl.col("mechanical").cast(pl.Int16),
        pl.col(ebike).cast(pl.Int16).alias("ebike"),
        docks.clip(lower_bound=0).cast(pl.Int16).alias("docks"),
        is_open.alias("is_installed"),
        is_open.alias("is_renting"),
        is_open.alias("is_returning"),
        pl.lit(None, dtype=_UTC).alias("last_reported"),
    ).sort("station_id", "fetched_at")


def _synthetic_id(name: str) -> int:
    return -(zlib.crc32(name.encode("utf-8")) % 1_000_000_000) - 1
