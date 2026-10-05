"""Raw snapshot format, shared by the collector and the daily compaction."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from velib.gbfs import StatusSnapshot

STATUS_FILENAME = "station_status.csv"
META_FILENAME = "snapshot_meta.json"
STATUS_COLUMNS = (
    "station_id",
    "mechanical",
    "ebike",
    "docks",
    "is_installed",
    "is_renting",
    "is_returning",
    "last_reported",
)

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MESSAGE_PATTERN = re.compile(r"^snapshot fetched_at=(\S+) feed_updated_at=(\S+)$")


@dataclass(frozen=True)
class SnapshotTimes:
    """When a snapshot was fetched, and when the feed was last updated."""

    fetched_at: datetime
    feed_updated_at: datetime


def format_time(moment: datetime) -> str:
    """Formats a timezone-aware datetime as ISO 8601 UTC, to the second."""
    return moment.astimezone(UTC).strftime(_TIME_FORMAT)


def parse_time(text: str) -> datetime:
    """Parses a time written by format_time."""
    return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=UTC)


def format_commit_message(times: SnapshotTimes) -> str:
    """Builds the commit message that records the times of a snapshot."""
    fetched = format_time(times.fetched_at)
    updated = format_time(times.feed_updated_at)
    return f"snapshot fetched_at={fetched} feed_updated_at={updated}"


def parse_commit_message(message: str) -> SnapshotTimes | None:
    """Reads the times recorded in a snapshot commit message.

    Args:
        message: Full commit message.

    Returns:
        The times, or None when the first line is not a snapshot message.
    """
    lines = message.splitlines()
    match = _MESSAGE_PATTERN.match(lines[0]) if lines else None
    if match is None:
        return None
    return SnapshotTimes(fetched_at=parse_time(match[1]), feed_updated_at=parse_time(match[2]))


def write_snapshot(snapshot: StatusSnapshot, output_dir: Path, fetched_at: datetime) -> str:
    """Writes station_status.csv and snapshot_meta.json for one snapshot.

    Args:
        snapshot: Checked station statuses.
        output_dir: Directory for both files, created when missing.
        fetched_at: Timezone-aware time of the download.

    Returns:
        The commit message for this snapshot.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stations = sorted(snapshot.stations, key=lambda station: station.station_id)
    with (output_dir / STATUS_FILENAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(STATUS_COLUMNS)
        for station in stations:
            writer.writerow(
                (
                    station.station_id,
                    station.mechanical,
                    station.ebike,
                    station.docks,
                    int(station.is_installed),
                    int(station.is_renting),
                    int(station.is_returning),
                    station.last_reported,
                )
            )
    times = SnapshotTimes(fetched_at=fetched_at, feed_updated_at=snapshot.feed_updated_at)
    meta = {
        "fetched_at": format_time(times.fetched_at),
        "feed_updated_at": format_time(times.feed_updated_at),
        "stations": len(stations),
    }
    (output_dir / META_FILENAME).write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return format_commit_message(times)
