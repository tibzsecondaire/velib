"""Tests for the replay map."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import polars as pl

from velib.replay import build_replay, render_replay_html

STATIONS = pl.DataFrame(
    {
        "station_id": [1, 2, 3],
        "name": ["Alpha", "Bravo", "Charlie"],
        "lat": [48.85, 48.86, None],
        "lon": [2.35, 2.36, None],
    }
)


def _frame() -> pl.DataFrame:
    rows = [
        (datetime(2025, 12, 9, 7, 5, tzinfo=UTC), 1, 2, 0, 8),
        (datetime(2025, 12, 9, 7, 5, tzinfo=UTC), 2, 6, 0, 4),
        (datetime(2025, 12, 9, 7, 5, tzinfo=UTC), 3, 1, 0, 1),
        (datetime(2025, 12, 9, 8, 5, tzinfo=UTC), 1, 4, 0, 6),
    ]
    return pl.DataFrame(
        rows,
        schema={
            "fetched_at": pl.Datetime("us", "UTC"),
            "station_id": pl.Int64(),
            "mechanical": pl.Int16(),
            "ebike": pl.Int16(),
            "docks": pl.Int16(),
        },
        orient="row",
    ).with_columns(pl.lit(True).alias("is_installed"), pl.lit(True).alias("is_renting"))


def test_build_replay_lists_slots_located_stations_and_rates() -> None:
    payload = build_replay(_frame(), STATIONS, slot="1h")
    assert payload["times"] == ["mar. 09/12 08:00", "mar. 09/12 09:00"]
    assert payload["stations"] == [[1, "Alpha", 48.85, 2.35], [2, "Bravo", 48.86, 2.36]]
    assert payload["rates"] == [[20, 40], [60, -1]]
    assert payload["city"] == [0.4333, 0.4]


def test_render_replay_html_embeds_the_data_safely() -> None:
    payload = {
        "times": ["t"],
        "stations": [[1, "</script><b>x", 48.8, 2.3]],
        "rates": [[5]],
        "city": [0.05],
    }
    page = render_replay_html(payload, "Vélib' <replay>")
    assert "<title>Vélib&#x27; &lt;replay&gt;</title>" in page
    assert "maplibre-gl@6.12.0" in page
    match = re.search(
        r'<script type="application/json" id="replay-data">(.*?)</script>', page, re.S
    )
    assert match is not None
    assert json.loads(match[1]) == payload
