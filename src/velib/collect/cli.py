"""Command line entry point: velib-collect snapshot | compact."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from velib import gbfs
from velib.collect.compact import (
    STATIONS_PATH,
    CompactionError,
    compact_day,
    write_station_information,
)
from velib.collect.snapshot import write_snapshot

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-collect command line.

    Args:
        argv: Arguments without the program name. Defaults to sys.argv[1:].

    Returns:
        The process exit code.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            return _snapshot(args.output_dir)
        return _compact(args.repo, args.day)
    except (gbfs.GbfsError, CompactionError) as error:
        logger.error("%s", error)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="velib-collect", description="Collect Vélib' station data for velib-data."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="Fetch station_status and write the raw files.")
    snapshot.add_argument(
        "--output-dir", type=Path, required=True, help="Directory that receives the raw files."
    )
    compact = commands.add_parser("compact", help="Compact the snapshots of one UTC day.")
    compact.add_argument(
        "--day",
        type=date.fromisoformat,
        default=None,
        help="UTC day as YYYY-MM-DD. Defaults to yesterday.",
    )
    compact.add_argument(
        "--repo", type=Path, default=Path(), help="Path to the velib-data checkout."
    )
    return parser


def _snapshot(output_dir: Path) -> int:
    with gbfs.make_client() as client:
        snapshot = gbfs.fetch_station_status(client)
    fetched_at = datetime.now(UTC)
    message = write_snapshot(snapshot, output_dir, fetched_at)
    logger.info("wrote %d stations to %s", len(snapshot.stations), output_dir)
    sys.stdout.write(message + "\n")
    return 0


def _compact(repo: Path, day: date | None) -> int:
    target = day or datetime.now(UTC).date() - timedelta(days=1)
    summary = compact_day(repo, target)
    with gbfs.make_client() as client:
        stations = gbfs.fetch_station_information(client)
    write_station_information(stations, repo / STATIONS_PATH)
    logger.info(
        "compacted %s: %d snapshots, %d rows, largest gap %.1f min",
        target.isoformat(),
        summary.snapshots,
        summary.rows,
        summary.max_gap_minutes,
    )
    return 0
