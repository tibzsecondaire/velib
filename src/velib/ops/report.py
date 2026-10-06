"""Report and map of the operator analyses: rebalancing, immobile bikes, out-of-service docks."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Any

import duckdb
import polars as pl

from velib.db import query
from velib.ops.breakdowns import (
    MAX_FLOOR,
    MIN_DAYS,
    MIN_DEPARTURES,
    MIN_SNAPSHOTS,
    MIN_SNAPSHOTS_AT_FLOOR,
    has_real_docks,
    immobile_bikes,
    immobile_per_day,
    unavailable_docks,
)
from velib.ops.regulation import (
    MAX_GAP_MINUTES,
    THRESHOLD,
    activity_by_hour,
    detect_operations,
    fill_before,
    hourly_shares,
    summarize_by_day,
    top_stations,
)
from velib.replay import MAPLIBRE_URL, STYLE_URL

SENSITIVITY = (8, 10, 12)
KINDS = {"ebike": "Electric", "mechanical": "Mechanical"}


@dataclass(frozen=True)
class Findings:
    """Results of the operator analyses on one source."""

    first: datetime
    last: datetime
    stations: pl.DataFrame
    operations: pl.DataFrame
    sensitivity: dict[int, int]
    activity: pl.DataFrame
    immobile: pl.DataFrame
    unavailable: pl.DataFrame | None


def analyse(connection: duckdb.DuckDBPyConnection) -> Findings:
    """Runs the rebalancing and breakdown analyses on the views of a connection."""
    first, last = query(connection, "SELECT min(local_time), max(local_time) FROM steps").row(0)
    return Findings(
        first=first,
        last=last,
        stations=query(connection, "SELECT * FROM stations"),
        operations=detect_operations(connection),
        sensitivity={
            threshold: detect_operations(connection, threshold=threshold).height
            for threshold in SENSITIVITY
        },
        activity=activity_by_hour(connection),
        immobile=immobile_bikes(connection),
        unavailable=unavailable_docks(connection) if has_real_docks(connection) else None,
    )


def render_markdown(findings: Findings, label: str) -> str:
    """Writes the report of the analyses as Markdown."""
    operations = findings.operations
    lines = [
        f"# Rebalancing and out-of-service bikes on {label}",
        "",
        f"Snapshots from {findings.first:%Y-%m-%d %H:%M} to {findings.last:%Y-%m-%d %H:%M}, "
        f"Paris time, {findings.stations.height} stations.",
        "",
        "## Rebalancing operations",
        "",
        f"An operation is a change of at least {THRESHOLD} bikes in a station between two "
        f"snapshots at most {MAX_GAP_MINUTES} minutes apart. Consecutive steps in the same "
        "direction count as one operation.",
        "",
        _sensitivity_sentence(findings.sensitivity),
        "",
        *_table(
            ["Day", "Operations", "Bikes added", "Bikes removed"],
            summarize_by_day(operations).rows(),
        ),
    ]
    fills = fill_before(operations)
    if fills["additions"] is not None and fills["removals"] is not None:
        lines += [
            "",
            f"Just before an operation, stations that receive bikes are "
            f"{fills['additions']:.0%} full on average, and stations that lose bikes are "
            f"{fills['removals']:.0%} full.",
        ]
    shares = hourly_shares(operations, findings.activity)
    receivers, givers = top_stations(operations)
    lines += [
        "",
        "### Operations per hour, compared with all bike moves",
        "",
        *_table(
            ["Hour", "Share of operations", "Share of all bike moves"],
            [
                (f"{hour:02d}:00", f"{ops:.1f}%", f"{moves:.1f}%")
                for hour, ops, moves in shares.rows()
            ],
        ),
        "",
        "### Stations that receive the most bikes",
        "",
        *_table(
            ["Station", "Operations", "Bikes added"],
            receivers.select("name", "operations", "bikes_added").rows(),
        ),
        "",
        "### Stations that lose the most bikes",
        "",
        *_table(
            ["Station", "Operations", "Bikes removed"],
            givers.select("name", "operations", "bikes_removed").rows(),
        ),
        "",
        *_immobile_section(findings.immobile),
        "",
        *_docks_section(findings.unavailable),
    ]
    return "\n".join(lines) + "\n"


def map_payload(findings: Findings) -> dict[str, Any]:
    """Figures of each located station for the map, and the totals of the summary line."""
    operations = findings.operations.group_by("station_id").agg(
        pl.len().alias("operations"),
        pl.col("bikes_moved").clip(lower_bound=0).sum().alias("added"),
        (-pl.col("bikes_moved").clip(upper_bound=0)).sum().alias("removed"),
    )
    immobile = findings.immobile.group_by("station_id").agg(
        *[
            pl.col(column).filter(pl.col("kind") == kind).max().alias(f"{kind}_{column}")
            for kind in KINDS
            for column in ("days", "bikes")
        ]
    )
    unavailable = (
        findings.unavailable.select("station_id", pl.col("unavailable").round(2))
        if findings.unavailable is not None
        else findings.stations.select("station_id", pl.lit(None, pl.Float64).alias("unavailable"))
    )
    counts = ["operations", "added", "removed"] + [
        f"{kind}_{column}" for kind in KINDS for column in ("days", "bikes")
    ]
    table = (
        findings.stations.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null())
        .select("station_id", "name", "lat", "lon")
        .join(operations, on="station_id", how="left")
        .join(immobile, on="station_id", how="left")
        .join(unavailable, on="station_id", how="left")
        .with_columns(pl.col(counts).fill_null(0))
        .sort("station_id")
    )
    runs = findings.immobile.group_by("kind").len()
    return {
        "has_unavailable": findings.unavailable is not None,
        "totals": {
            "operations": findings.operations.height,
            "added": table.select(pl.col("added").sum()).item(),
            "removed": table.select(pl.col("removed").sum()).item(),
            "ebike_runs": _count(runs, "ebike"),
            "mechanical_runs": _count(runs, "mechanical"),
            "unavailable": (
                round(findings.unavailable.select(pl.col("unavailable").sum()).item(), 1)
                if findings.unavailable is not None
                else None
            ),
        },
        "stations": table.drop("station_id").to_dicts(),
    }


def render_map_html(payload: dict[str, Any], title: str) -> str:
    """Builds the standalone map page, with the data embedded as JSON."""
    template = files("velib.ops").joinpath("map.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        template.replace("__TITLE__", html.escape(title))
        .replace("__MAPLIBRE__", MAPLIBRE_URL)
        .replace("__STYLE__", STYLE_URL)
        .replace("__DATA__", data)
    )


def _sensitivity_sentence(sensitivity: dict[int, int]) -> str:
    thresholds = list(sensitivity)
    counts = [str(sensitivity[threshold]) for threshold in thresholds]
    return (
        f"Operations with a threshold of {', '.join(map(str, thresholds[:-1]))} and "
        f"{thresholds[-1]} bikes: {', '.join(counts[:-1])} and {counts[-1]}."
    )


def _immobile_section(immobile: pl.DataFrame) -> list[str]:
    per_day = immobile_per_day(immobile)
    rows = []
    for kind, label in KINDS.items():
        runs = immobile.filter(pl.col("kind") == kind)
        daily = per_day.filter(pl.col("kind") == kind).select(pl.col("bikes").mean()).item()
        rows.append(
            (
                label,
                runs.height,
                runs.get_column("station_id").n_unique(),
                f"{daily or 0:.0f}",
                runs.get_column("days").max() or 0,
            )
        )
    longest = immobile.head(10).with_columns(pl.col("kind").replace_strict(KINDS))
    return [
        "## Immobile bikes",
        "",
        f"A Paris day counts when a station has at least {MIN_SNAPSHOTS} snapshots and "
        f"{MIN_DEPARTURES} departures of a bike type, and keeps falling back to the same 1 to "
        f"{MAX_FLOOR} bikes of that type for at least {MIN_SNAPSHOTS_AT_FLOOR * 5} minutes in "
        f"total. At least {MIN_DAYS} consecutive such days make an immobilisation: probably "
        "broken bikes or, for electric bikes, discharged ones. Larger floors that a station "
        "only touches are a surplus of bikes, not stuck bikes.",
        "",
        *_table(
            ["Type", "Immobilisations", "Stations", "Immobile bikes per day", "Longest, in days"],
            rows,
        ),
        "",
        "### Longest immobilisations",
        "",
        *_table(
            ["Station", "Type", "From", "To", "Days", "Bikes"],
            longest.select("name", "kind", "first_day", "last_day", "days", "bikes").rows(),
        ),
    ]


def _docks_section(unavailable: pl.DataFrame | None) -> list[str]:
    lines = ["## Out-of-service docks", ""]
    if unavailable is None:
        return [
            *lines,
            "This source estimates the free docks from the capacity, so out-of-service docks "
            "cannot be measured. velib-data has the real free docks.",
        ]
    total, affected = unavailable.select(
        pl.col("unavailable").sum(), (pl.col("share_of_time") > 0).mean()
    ).row(0)
    return [
        *lines,
        "The feed counts neither broken docks nor disabled bikes: capacity minus bikes minus "
        "free docks estimates them.",
        "",
        f"On average, {total:.0f} docks are out of service at a time. {affected or 0:.0%} of the "
        "stations have at least one at some point.",
        "",
        *_table(
            ["Station", "Out-of-service docks on average", "Share of the time"],
            [
                (name, f"{mean:.1f}", f"{share:.0%}")
                for name, mean, share in unavailable.head(10)
                .select("name", "unavailable", "share_of_time")
                .rows()
            ],
        ),
    ]


def _table(header: list[str], rows: list[tuple[Any, ...]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows),
    ]


def _count(runs: pl.DataFrame, kind: str) -> int:
    match = runs.filter(pl.col("kind") == kind)
    return int(match.get_column("len").item()) if match.height else 0
