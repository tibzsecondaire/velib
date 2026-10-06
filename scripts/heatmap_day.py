"""Plot the fill-rate heatmap of one day, in Paris time, from velib-data or an imported archive.

Usage:
    uv run --group analysis python scripts/heatmap_day.py 2026-10-06
    uv run --group analysis python scripts/heatmap_day.py --source kaggle 2025-12-10
    uv run --group analysis python scripts/heatmap_day.py --source lovasoa --slot 30m 2021-03-25
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

import plotly.graph_objects as go
import polars as pl

from velib import DATA_DIR
from velib.archives import ARCHIVES_DIR
from velib.dataset import DATA_SOURCE, load_day, load_stations
from velib.heatmap import fill_rate_by_slot

logger = logging.getLogger("heatmap_day")


def resolve_source(value: str) -> str | Path:
    """Turns the --source option into a load_day source."""
    if value == "velib-data":
        return DATA_SOURCE
    for candidate in (ARCHIVES_DIR / value, Path(value)):
        if candidate.is_dir():
            return candidate
    raise SystemExit(
        f"unknown source {value!r}: import it first with `velib-archives import {value}`"
    )


def load_paris_day(day: date, source: str | Path) -> pl.DataFrame:
    """Loads the 2 UTC files that cover a Paris day: the day before and the day itself."""
    frames = []
    for utc_day in (day - timedelta(days=1), day):
        try:
            frames.append(load_day(utc_day, source=source))
        except FileNotFoundError:
            logger.warning("no compacted data for %s (UTC)", utc_day.isoformat())
    if not frames:
        raise SystemExit(f"no compacted data around {day.isoformat()}")
    return pl.concat(frames)


def build_figure(day: date, source: str | Path, label: str, slot: str = "15m") -> go.Figure:
    """Builds the heatmap: one row per station, one column per time slot."""
    rates = fill_rate_by_slot(load_paris_day(day, source), day, slot=slot).with_columns(
        pl.col("slot").dt.strftime("%H:%M").alias("label")
    )
    wide = rates.pivot(
        on="label", index="station_id", values="fill_rate", aggregate_function="mean"
    )
    labels = sorted(column for column in wide.columns if column != "station_id")
    order = rates.group_by("station_id").agg(pl.col("fill_rate").mean().alias("mean_rate"))
    names = load_stations(source=source).select("station_id", "name")
    table = (
        order.join(wide, on="station_id", how="left")
        .join(names, on="station_id", how="left")
        .sort("mean_rate", nulls_last=True)
    )
    station_labels = (
        table.select(pl.coalesce(pl.col("name"), pl.col("station_id").cast(pl.String)))
        .to_series()
        .to_list()
    )
    figure = go.Figure(
        go.Heatmap(
            z=table.select(labels).rows(),
            x=labels,
            y=station_labels,
            colorscale="RdYlGn",
            zmin=0,
            zmax=1,
            colorbar={"title": {"text": "remplissage"}},
        )
    )
    suffix = "" if label == "velib-data" else f" · archive {label}"
    if slot != "15m":
        suffix += f" · créneaux de {slot}"
    figure.update_layout(
        title=f"Remplissage des stations Vélib' le {day:%d/%m/%Y} (heure de Paris){suffix}",
        height=1600,
        xaxis={"title": "heure"},
        yaxis={"title": "stations, de la plus vide à la plus pleine", "showticklabels": False},
    )
    return figure


def main() -> None:
    """Writes the heatmap of the requested day to data/figures/."""
    parser = argparse.ArgumentParser(description="Plot the fill-rate heatmap of one Paris day.")
    parser.add_argument("day", type=date.fromisoformat, help="Day in Paris time, as YYYY-MM-DD.")
    parser.add_argument(
        "--source",
        default="velib-data",
        help="velib-data (default), an imported archive such as kaggle or lovasoa, or a directory.",
    )
    parser.add_argument(
        "--slot",
        default="15m",
        help="Slot length as a polars duration, for example 30m or 1h for sparse archives.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    label = Path(args.source).name
    prefix = "heatmap" if label == "velib-data" else f"heatmap_{label}"
    if args.slot != "15m":
        prefix += f"_{args.slot}"
    output = DATA_DIR / "figures" / f"{prefix}_{args.day.isoformat()}.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    build_figure(args.day, resolve_source(args.source), label, args.slot).write_html(
        output, include_plotlyjs="cdn"
    )
    logger.info("wrote %s", output)


if __name__ == "__main__":
    main()
