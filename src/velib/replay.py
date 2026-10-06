"""Data and page of the map that replays the fill rate of every station, slot by slot."""

from __future__ import annotations

import html
import json
from datetime import datetime
from importlib.resources import files
from typing import Any

import polars as pl

from velib.heatmap import city_rhythm, fill_rate_over_time

WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
MAPLIBRE_URL = "https://cdn.jsdelivr.net/npm/maplibre-gl@6.12.0/dist/"
STYLE_URL = "https://tiles.openfreemap.org/styles/positron"


def build_replay(
    frame: pl.DataFrame, stations: pl.DataFrame, *, slot: str = "1h"
) -> dict[str, Any]:
    """Prepares the fill rate of every located station, per local slot, for the replay page.

    Args:
        frame: Snapshots of the period, in the daily schema.
        stations: Station information with station_id, name, lat and lon.
        slot: Slot length, as a polars duration string.

    Returns:
        times: one label per slot, such as "mar. 09/12 08:00".
        stations: [station_id, name, lat, lon] for each station with a position.
        rates: for each of these stations, its fill rate in percent per slot, -1 when unknown.
        city: mean fill rate of all open stations per slot, None when unknown.
    """
    rates = fill_rate_over_time(frame, slot=slot)
    slots = rates.select("slot").unique().sort("slot").with_row_index("step")
    located = stations.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null())
    shown = (
        rates.select("station_id")
        .unique()
        .join(located.select("station_id", "name", "lat", "lon"), on="station_id")
        .sort("station_id")
    )
    percent = (
        shown.select("station_id")
        .join(slots, how="cross")
        .join(rates, on=["station_id", "slot"], how="left")
        .sort("station_id", "step")
        .select(
            "station_id",
            pl.when(pl.col("fill_rate").is_not_null())
            .then((pl.col("fill_rate") * 100).round().cast(pl.Int16))
            .otherwise(-1)
            .alias("value"),
        )
        .group_by("station_id", maintain_order=True)
        .agg(pl.col("value"))
    )
    city = slots.join(
        city_rhythm(frame, slot=slot).select("slot", "fill_rate"), on="slot", how="left"
    ).sort("step")
    return {
        "times": [_label(moment) for moment in slots.get_column("slot").to_list()],
        "stations": [
            [row["station_id"], row["name"], row["lat"], row["lon"]]
            for row in shown.iter_rows(named=True)
        ],
        "rates": percent.get_column("value").to_list(),
        "city": [
            None if value is None else round(value, 4)
            for value in city.get_column("fill_rate").to_list()
        ],
    }


def render_replay_html(payload: dict[str, Any], title: str) -> str:
    """Builds the standalone replay page, with the data embedded as JSON."""
    template = files("velib").joinpath("replay.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        template.replace("__TITLE__", html.escape(title))
        .replace("__MAPLIBRE__", MAPLIBRE_URL)
        .replace("__STYLE__", STYLE_URL)
        .replace("__DATA__", data)
    )


def _label(moment: datetime) -> str:
    return f"{WEEKDAYS[moment.weekday()]} {moment:%d/%m %H:%M}"
