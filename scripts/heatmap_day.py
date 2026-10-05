"""Plot the fill-rate heatmap of one day, in Paris time, from velib-data.

Usage: uv run --group analysis python scripts/heatmap_day.py 2026-10-06
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import plotly.graph_objects as go
import polars as pl

from velib import DATA_DIR
from velib.dataset import load_day, load_stations
from velib.heatmap import fill_rate_by_slot

logger = logging.getLogger("heatmap_day")


def load_paris_day(day: date) -> pl.DataFrame:
    """Loads the 2 UTC files that cover a Paris day: the day before and the day itself."""
    frames = []
    for utc_day in (day - timedelta(days=1), day):
        try:
            frames.append(load_day(utc_day))
        except FileNotFoundError:
            logger.warning("no compacted data for %s (UTC)", utc_day.isoformat())
    if not frames:
        raise SystemExit(f"no compacted data around {day.isoformat()}")
    return pl.concat(frames)


def build_figure(day: date) -> go.Figure:
    """Builds the heatmap: one row per station, one column per 15-minute slot."""
    rates = fill_rate_by_slot(load_paris_day(day), day).with_columns(
        pl.col("slot").dt.strftime("%H:%M").alias("label")
    )
    wide = rates.pivot(
        on="label", index="station_id", values="fill_rate", aggregate_function="mean"
    )
    labels = sorted(column for column in wide.columns if column != "station_id")
    order = rates.group_by("station_id").agg(pl.col("fill_rate").mean().alias("mean_rate"))
    names = load_stations().select("station_id", "name")
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
    figure.update_layout(
        title=f"Remplissage des stations Vélib' le {day:%d/%m/%Y} (heure de Paris)",
        height=1600,
        xaxis={"title": "heure"},
        yaxis={"title": "stations, de la plus vide à la plus pleine", "showticklabels": False},
    )
    return figure


def main() -> None:
    """Writes the heatmap of the requested day to data/figures/."""
    parser = argparse.ArgumentParser(description="Plot the fill-rate heatmap of one Paris day.")
    parser.add_argument("day", type=date.fromisoformat, help="Day in Paris time, as YYYY-MM-DD.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    output = DATA_DIR / "figures" / f"heatmap_{args.day.isoformat()}.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    build_figure(args.day).write_html(output, include_plotlyjs="cdn")
    logger.info("wrote %s", output)


if __name__ == "__main__":
    main()
