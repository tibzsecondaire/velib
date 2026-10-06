"""Plot Vélib' fill rates over a period: the city rhythm by day and hour, and every station.

Usage:
    uv run --group analysis python scripts/heatmap_period.py --source kaggle
    uv run --group analysis python scripts/heatmap_period.py --source velib-data --start 2026-10-06
"""

from __future__ import annotations

import argparse
import html
import logging
from datetime import date
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

from velib import DATA_DIR
from velib.archives import resolve_source
from velib.dataset import load_period, load_stations
from velib.heatmap import PARIS_TIME_ZONE, city_rhythm, fill_rate_over_time

logger = logging.getLogger("heatmap_period")

WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
HOURS = [f"{hour:02d}:00" for hour in range(24)]


def rhythm_figure(frame: pl.DataFrame) -> go.Figure:
    """City rhythm: mean fill rate and share of empty stations, by day and hour."""
    rhythm = city_rhythm(frame, slot="1h")
    days: list[date] = rhythm.get_column("day").unique().sort().to_list()
    labels = [f"{WEEKDAYS[day.weekday()]} {day:%d/%m}" for day in days]

    def matrix(column: str) -> list[list[float | None]]:
        table = rhythm.pivot(on="time_of_day", index="day", values=column).sort("day")
        return [[row.get(hour) for hour in HOURS] for row in table.iter_rows(named=True)]

    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Remplissage moyen des stations", "Part de stations vides"),
        horizontal_spacing=0.14,
    )
    figure.add_trace(
        go.Heatmap(
            z=matrix("fill_rate"),
            x=HOURS,
            y=labels,
            colorscale="RdYlGn",
            zmin=0,
            zmax=1,
            colorbar={"title": {"text": "remplissage"}, "tickformat": ".0%", "x": 0.43},
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Heatmap(
            z=matrix("empty_share"),
            x=HOURS,
            y=labels,
            colorscale="Reds",
            zmin=0,
            colorbar={"title": {"text": "vides"}, "tickformat": ".0%"},
        ),
        row=1,
        col=2,
    )
    figure.update_yaxes(autorange="reversed")
    figure.update_layout(
        title="Rythme de la ville, par jour et par heure (heure de Paris)",
        height=max(420, 34 * len(days) + 180),
    )
    return figure


def stations_figure(frame: pl.DataFrame, source: str | Path, slot: str) -> go.Figure:
    """Every station over the period: one row per station, one column per slot."""
    rates = fill_rate_over_time(frame, slot=slot).with_columns(
        pl.col("slot").dt.strftime("%Y-%m-%dT%H:%M").alias("key")
    )
    wide = rates.pivot(on="key", index="station_id", values="fill_rate")
    slots = rates.select("slot", "key").unique().sort("slot")
    keys = slots.get_column("key").to_list()
    order = rates.group_by("station_id").agg(pl.col("fill_rate").mean().alias("mean_rate"))
    names = load_stations(source=source).select("station_id", "name")
    table = (
        order.join(wide, on="station_id", how="left")
        .join(names, on="station_id", how="left")
        .sort("mean_rate", nulls_last=True)
    )
    labels = (
        table.select(pl.coalesce(pl.col("name"), pl.col("station_id").cast(pl.String)))
        .to_series()
        .to_list()
    )
    figure = go.Figure(
        go.Heatmap(
            z=table.select(keys).rows(),
            x=slots.get_column("slot").to_list(),
            y=labels,
            colorscale="RdYlGn",
            zmin=0,
            zmax=1,
            colorbar={"title": {"text": "remplissage"}, "tickformat": ".0%"},
        )
    )
    figure.update_layout(
        title=f"Remplissage de chaque station, par créneau de {slot} (heure de Paris)",
        height=1600,
        xaxis={"title": "jour et heure"},
        yaxis={"title": "stations, de la plus vide à la plus pleine", "showticklabels": False},
    )
    return figure


def write_page(path: Path, title: str, figures: list[go.Figure]) -> None:
    """Writes the figures one below the other in a single HTML page."""
    parts = [
        figure.to_html(full_html=False, include_plotlyjs="cdn" if index == 0 else False)
        for index, figure in enumerate(figures)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head><body>{''.join(parts)}</body></html>\n",
        encoding="utf-8",
    )


def main() -> None:
    """Writes the period heatmaps to data/figures/."""
    parser = argparse.ArgumentParser(description="Plot Vélib' fill rates over a period.")
    parser.add_argument(
        "--source",
        default="kaggle",
        help="velib-data, an imported archive such as kaggle or lovasoa, or a directory.",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="First Paris day.")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Last Paris day.")
    parser.add_argument("--slot", default="1h", help="Slot length of the station heatmap.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    source = resolve_source(args.source)
    try:
        frame = load_period(source=source, start=args.start, end=args.end)
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from error
    local_day = pl.col("fetched_at").dt.convert_time_zone(PARIS_TIME_ZONE).dt.date()
    first, last = frame.select(local_day.min(), local_day.max().alias("last")).row(0)
    label = Path(args.source).name
    output = DATA_DIR / "figures" / f"period_{label}_{first}_{last}.html"
    write_page(
        output,
        f"Vélib' du {first} au {last} ({label})",
        [rhythm_figure(frame), stations_figure(frame, source, args.slot)],
    )
    logger.info("wrote %s", output)


if __name__ == "__main__":
    main()
