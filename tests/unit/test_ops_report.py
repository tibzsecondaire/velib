"""Tests for the report and the map of the operator analyses."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.unit.sources import write_source
from velib.db import connect
from velib.ops.cli import main
from velib.ops.report import Findings, analyse, map_payload, render_map_html, render_markdown

T0 = datetime(2025, 12, 9, 2, 0, tzinfo=UTC)


def _source(tmp_path: Path, *, real_docks: bool = True) -> Path:
    bikes = [2, 2, 12, 20, 21, 9]
    rows = [
        (T0 + timedelta(minutes=5 * step), 1, count, 0, 25 - count)
        for step, count in enumerate(bikes)
    ]
    rows.append((T0, 2, 5, 0, 15))
    return write_source(tmp_path / "source", rows, real_docks=real_docks)


def _findings(tmp_path: Path, *, real_docks: bool = True) -> Findings:
    return analyse(connect(_source(tmp_path, real_docks=real_docks)))


def _embedded(page: str) -> Any:
    match = re.search(r'<script type="application/json" id="ops-data">(.*?)</script>', page, re.S)
    assert match is not None
    return json.loads(match[1])


def test_render_markdown_reports_operations_and_docks(tmp_path: Path) -> None:
    report = render_markdown(_findings(tmp_path), "tiny")
    assert report.startswith("# Rebalancing and out-of-service bikes on tiny\n")
    assert "| 2025-12-09 | 2 | 18 | 12 |" in report
    assert "| Alpha | 2 | 18 |" in report
    assert "Operations with a threshold of 8, 10 and 12 bikes: 2, 2 and 1." in report
    assert "| Alpha | 5.0 | 100% |" in report


def test_render_markdown_explains_why_archives_have_no_docks(tmp_path: Path) -> None:
    report = render_markdown(_findings(tmp_path, real_docks=False), "archive")
    assert "estimates the free docks from the capacity" in report


def test_map_payload_sums_each_station_and_embeds_safely(tmp_path: Path) -> None:
    payload = map_payload(_findings(tmp_path))
    alpha = next(station for station in payload["stations"] if station["name"] == "Alpha")
    assert (alpha["operations"], alpha["added"], alpha["removed"]) == (2, 18, 12)
    assert alpha["unavailable"] == 5.0
    assert payload["totals"]["operations"] == 2
    page = render_map_html(payload | {"stations": [alpha | {"name": "</script>x"}]}, "<carte>")
    assert "<title>&lt;carte&gt;</title>" in page
    assert not re.search(r"__[A-Z]+__", page)
    assert _embedded(page)["stations"][0]["name"] == "</script>x"


def test_velib_ops_report_writes_the_report_and_the_map(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["report", "--source", str(_source(tmp_path)), "--out", str(out)]) == 0
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# Rebalancing")
    assert "ops-data" in (out / "map.html").read_text(encoding="utf-8")
