"""Tests for the replay map."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

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
ROWS = [
    (datetime(2025, 12, 9, 7, 5, tzinfo=UTC), 1, 2, 0, 8),
    (datetime(2025, 12, 9, 7, 5, tzinfo=UTC), 2, 6, 0, 4),
    (datetime(2025, 12, 9, 7, 5, tzinfo=UTC), 3, 1, 0, 1),
    (datetime(2025, 12, 9, 8, 5, tzinfo=UTC), 1, 4, 0, 6),
]


def _snapshots(rows: list[tuple[datetime, int, int, int, int]]) -> pl.DataFrame:
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


def _embedded(page: str) -> Any:
    match = re.search(
        r'<script type="application/json" id="replay-data">(.*?)</script>', page, re.S
    )
    assert match is not None
    return json.loads(match[1])


def _payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "times": ["t0", "t1"],
        "days": [[0, "d0"]],
        "minutes": 15,
        "stations": [[1, "Alpha", 48.8, 2.3], [2, "Bravo", 48.9, 2.4]],
        "rates": [[5, -1], [100, 0]],
        "bikes": [[300, 0], [3, -1]],
        "city": [0.05, None],
    }
    return payload | changes


def test_build_replay_lists_slots_located_stations_rates_and_bikes() -> None:
    payload = build_replay(_snapshots(ROWS), STATIONS, slot="1h")
    assert payload["times"] == ["mar. 09/12 08:00", "mar. 09/12 09:00"]
    assert payload["minutes"] == 60
    assert payload["stations"] == [[1, "Alpha", 48.85, 2.35], [2, "Bravo", 48.86, 2.36]]
    assert payload["rates"] == [[20, 40], [60, -1]]
    assert payload["bikes"] == [[2, 4], [6, -1]]
    assert payload["city"] == [0.4333, 0.4]


def test_build_replay_defaults_to_quarter_hours_and_marks_each_paris_day() -> None:
    frame = _snapshots(
        [
            (datetime(2025, 12, 9, 22, 50, tzinfo=UTC), 1, 2, 0, 8),
            (datetime(2025, 12, 9, 23, 5, tzinfo=UTC), 1, 2, 0, 8),
            (datetime(2025, 12, 9, 23, 20, tzinfo=UTC), 1, 2, 0, 8),
        ]
    )
    payload = build_replay(frame, STATIONS)
    assert payload["times"] == ["mar. 09/12 23:45", "mer. 10/12 00:00", "mer. 10/12 00:15"]
    assert payload["days"] == [[0, "mar. 09/12"], [1, "mer. 10/12"]]
    assert payload["minutes"] == 15


def test_render_replay_html_embeds_the_data_safely() -> None:
    stations = [[1, "</script><b>x", 48.8, 2.3], [2, "Bravo", 48.9, 2.4]]
    page = render_replay_html(_payload(stations=stations), "Vélib' <replay>")
    assert "<title>Vélib&#x27; &lt;replay&gt;</title>" in page
    assert not re.search(r"__[A-Z]+__", page)
    assert _embedded(page)["stations"] == stations


def test_render_replay_html_packs_rates_and_bikes_into_bytes() -> None:
    embedded = _embedded(render_replay_html(_payload(), "replay"))
    assert embedded["rates"] == "Bf9kAA=="
    assert embedded["bikes"] == "/gAD/w=="
    assert embedded["times"] == ["t0", "t1"]
