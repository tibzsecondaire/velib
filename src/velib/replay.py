"""Data and page of the map that replays the fill rate and bikes of every station, slot by slot."""

from __future__ import annotations

import base64
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
MISSING = 255


def build_replay(
    frame: pl.DataFrame, stations: pl.DataFrame, *, slot: str = "15m"
) -> dict[str, Any]:
    """Prepares the fill rate and bikes of every located station, per local slot, for the page.

    Args:
        frame: Snapshots of the period, in the daily schema.
        stations: Station information with station_id, name, lat and lon.
        slot: Slot length, as a polars duration string.

    Returns:
        times: one label per slot, such as "mar. 09/12 08:00".
        days: [step, label] for the first slot of each local day, such as [0, "mar. 09/12"].
        minutes: slot length in minutes.
        stations: [station_id, name, lat, lon] for each station with a position.
        rates: for each of these stations, its fill rate in percent per slot, -1 when unknown.
        bikes: for each of these stations, its mean available bikes per slot, -1 when unknown.
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
    grid = (
        shown.select("station_id")
        .join(slots, how="cross")
        .join(rates, on=["station_id", "slot"], how="left")
        .sort("station_id", "step")
        .group_by("station_id", maintain_order=True)
        .agg(
            _rounded_or_missing(pl.col("fill_rate") * 100).alias("rates"),
            _rounded_or_missing(pl.col("bikes")).alias("bikes"),
        )
    )
    first_of_day = slots.filter(pl.col("slot").dt.date().is_first_distinct())
    city = slots.join(
        city_rhythm(frame, slot=slot).select("slot", "fill_rate"), on="slot", how="left"
    ).sort("step")
    return {
        "times": [_label(moment) for moment in slots.get_column("slot").to_list()],
        "days": [[step, _day(moment)] for step, moment in first_of_day.iter_rows()],
        "minutes": _slot_minutes(slot),
        "stations": [
            [row["station_id"], row["name"], row["lat"], row["lon"]]
            for row in shown.iter_rows(named=True)
        ],
        "rates": grid.get_column("rates").to_list(),
        "bikes": grid.get_column("bikes").to_list(),
        "city": [
            None if value is None else round(value, 4)
            for value in city.get_column("fill_rate").to_list()
        ],
    }


def render_replay_html(payload: dict[str, Any], title: str) -> str:
    """Builds the standalone replay page, with the data embedded as JSON.

    The rates and bikes become base64 text of one byte per station and slot, station after
    station: 255 marks a missing value, and larger counts are capped at 254.
    """
    template = files("velib").joinpath("replay.html").read_text(encoding="utf-8")
    packed = payload | {"rates": _pack(payload["rates"]), "bikes": _pack(payload["bikes"])}
    data = json.dumps(packed, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        template.replace("__TITLE__", html.escape(title))
        .replace("__MAPLIBRE__", MAPLIBRE_URL)
        .replace("__STYLE__", STYLE_URL)
        .replace("__DATA__", data)
    )


def _rounded_or_missing(value: pl.Expr) -> pl.Expr:
    return pl.when(value.is_not_null()).then(value.round().cast(pl.Int16)).otherwise(-1)


def _slot_minutes(slot: str) -> int:
    origin = datetime(2000, 1, 1)
    end = pl.select(pl.lit(origin).dt.offset_by(slot)).item()
    return int((end - origin).total_seconds() // 60)


def _pack(rows: list[list[int]]) -> str:
    values = bytes(
        MISSING if value < 0 else min(value, MISSING - 1) for row in rows for value in row
    )
    return base64.b64encode(values).decode("ascii")


def _day(moment: datetime) -> str:
    return f"{WEEKDAYS[moment.weekday()]} {moment:%d/%m}"


def _label(moment: datetime) -> str:
    return f"{_day(moment)} {moment:%H:%M}"
