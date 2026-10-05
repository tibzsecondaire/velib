"""Daily compaction: replays the snapshot commits of one UTC day into Parquet."""

from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from velib.collect.snapshot import (
    META_FILENAME,
    STATUS_FILENAME,
    SnapshotTimes,
    format_time,
    parse_commit_message,
)
from velib.gbfs import StationInfo

RAW_DIR = "raw"
DAILY_DIR = "daily"
INDEX_FILENAME = "index.csv"
STATIONS_PATH = Path("stations") / "station_information.csv"
INDEX_COLUMNS = (
    "date",
    "snapshots",
    "first_fetch",
    "last_fetch",
    "max_gap_minutes",
    "stations",
    "rows",
)
STATION_COLUMNS = ("station_id", "station_code", "name", "lat", "lon", "capacity")

_STATUS_SCHEMA = {
    "station_id": pl.Int64,
    "mechanical": pl.Int16,
    "ebike": pl.Int16,
    "docks": pl.Int16,
    "is_installed": pl.Int8,
    "is_renting": pl.Int8,
    "is_returning": pl.Int8,
    "last_reported": pl.Int64,
}


class CompactionError(Exception):
    """A day cannot be compacted, for example because it has no snapshot."""


@dataclass(frozen=True)
class DaySummary:
    """One line of daily/index.csv."""

    day: date
    snapshots: int
    first_fetch: datetime
    last_fetch: datetime
    max_gap_minutes: float
    stations: int
    rows: int


def list_snapshot_commits(repo: Path, day: date) -> list[tuple[str, SnapshotTimes]]:
    """Lists the snapshot commits fetched during one UTC day.

    Args:
        repo: Path to the velib-data checkout.
        day: UTC day.

    Returns:
        Pairs of commit hash and snapshot times, oldest first.
    """
    output = _git(repo, "log", "--format=%H%x1f%s", "--", f"{RAW_DIR}/{META_FILENAME}")
    commits = []
    for line in output.splitlines():
        sha, _, subject = line.partition("\x1f")
        times = parse_commit_message(subject)
        if times is not None and times.fetched_at.date() == day:
            commits.append((sha, times))
    return sorted(commits, key=lambda commit: commit[1].fetched_at)


def load_day_frame(repo: Path, commits: list[tuple[str, SnapshotTimes]]) -> pl.DataFrame:
    """Reads the raw CSV of each commit and stacks them into one typed frame.

    Args:
        repo: Path to the velib-data checkout.
        commits: Output of list_snapshot_commits.

    Returns:
        One row per station and per snapshot, sorted by station then time.
    """
    frames = []
    for sha, times in commits:
        content = _git(repo, "show", f"{sha}:{RAW_DIR}/{STATUS_FILENAME}")
        frame = pl.read_csv(io.StringIO(content), schema_overrides=_STATUS_SCHEMA)
        frames.append(
            frame.with_columns(
                pl.lit(times.fetched_at).alias("fetched_at"),
                pl.lit(times.feed_updated_at).alias("feed_updated_at"),
            )
        )
    return (
        pl.concat(frames)
        .select(
            "fetched_at",
            "feed_updated_at",
            "station_id",
            "mechanical",
            "ebike",
            "docks",
            pl.col("is_installed").cast(pl.Boolean),
            pl.col("is_renting").cast(pl.Boolean),
            pl.col("is_returning").cast(pl.Boolean),
            pl.from_epoch("last_reported", time_unit="s").dt.replace_time_zone("UTC"),
        )
        .sort("station_id", "fetched_at")
    )


def summarize(day: date, frame: pl.DataFrame) -> DaySummary:
    """Computes the index line of a compacted day."""
    fetches = frame.get_column("fetched_at").unique().sort()
    largest_gap = fetches.diff().max()
    max_gap_minutes = (
        round(largest_gap.total_seconds() / 60, 1) if isinstance(largest_gap, timedelta) else 0.0
    )
    return DaySummary(
        day=day,
        snapshots=fetches.len(),
        first_fetch=fetches[0],
        last_fetch=fetches[-1],
        max_gap_minutes=max_gap_minutes,
        stations=frame.get_column("station_id").n_unique(),
        rows=frame.height,
    )


def update_index(index_path: Path, summary: DaySummary) -> None:
    """Adds or replaces the line of a day in daily/index.csv, sorted by date."""
    lines: dict[str, list[str]] = {}
    if index_path.exists():
        with index_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                lines[row["date"]] = [row[column] for column in INDEX_COLUMNS]
    lines[summary.day.isoformat()] = [
        summary.day.isoformat(),
        str(summary.snapshots),
        format_time(summary.first_fetch),
        format_time(summary.last_fetch),
        f"{summary.max_gap_minutes:.1f}",
        str(summary.stations),
        str(summary.rows),
    ]
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(INDEX_COLUMNS)
        for key in sorted(lines):
            writer.writerow(lines[key])


def compact_day(repo: Path, day: date) -> DaySummary:
    """Writes daily/<day>.parquet and updates daily/index.csv for one UTC day.

    Args:
        repo: Path to the velib-data checkout, with the history of the day.
        day: UTC day to compact.

    Returns:
        The index line of the day.

    Raises:
        CompactionError: No snapshot commit was found for the day.
    """
    commits = list_snapshot_commits(repo, day)
    if not commits:
        raise CompactionError(f"no snapshot commit found for {day.isoformat()}")
    frame = load_day_frame(repo, commits)
    daily_dir = repo / DAILY_DIR
    daily_dir.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(daily_dir / f"{day.isoformat()}.parquet", compression="zstd")
    summary = summarize(day, frame)
    update_index(daily_dir / INDEX_FILENAME, summary)
    return summary


def write_station_information(stations: list[StationInfo], path: Path) -> None:
    """Writes the static station information as CSV, sorted by station_id."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(STATION_COLUMNS)
        for station in sorted(stations, key=lambda item: item.station_id):
            writer.writerow(
                (
                    station.station_id,
                    station.station_code,
                    station.name,
                    station.lat,
                    station.lon,
                    station.capacity,
                )
            )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout
