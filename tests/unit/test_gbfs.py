"""Tests for the GBFS client."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from velib import gbfs

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return gbfs.make_client(transport=httpx.MockTransport(handler))


def _status_document(stations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"lastUpdatedOther": 1791202552, "ttl": 3600, "data": {"stations": stations}}


def _raw_status(
    station_id: int, mechanical: int = 1, ebike: int = 2, docks: int = 3
) -> dict[str, Any]:
    return {
        "station_id": station_id,
        "num_bikes_available": mechanical + ebike,
        "num_bikes_available_types": [{"mechanical": mechanical}, {"ebike": ebike}],
        "num_docks_available": docks,
        "is_installed": 1,
        "is_renting": 1,
        "is_returning": 0,
        "last_reported": 1791202000,
        "stationCode": str(station_id),
    }


def test_requests_carry_the_explicit_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, json={})

    with _client(handler) as client:
        gbfs.fetch_feed(client, "station_status")
    assert seen == [gbfs.USER_AGENT]
    assert not gbfs.USER_AGENT.startswith("Python-urllib")


def test_fetch_feed_retries_server_errors_then_returns_document() -> None:
    answers = iter(
        [httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True})]
    )
    sleeps: list[float] = []
    with _client(lambda request: next(answers)) as client:
        document = gbfs.fetch_feed(client, "station_status", sleep=sleeps.append)
    assert document == {"ok": True}
    assert sleeps == [2.0, 4.0]


def test_fetch_feed_gives_up_after_three_attempts() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectTimeout("timed out", request=request)

    sleeps: list[float] = []
    with _client(handler) as client, pytest.raises(gbfs.GbfsError, match="ConnectTimeout"):
        gbfs.fetch_feed(client, "station_status", sleep=sleeps.append)
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_fetch_feed_fails_at_once_on_403() -> None:
    sleeps: list[float] = []
    with (
        _client(lambda request: httpx.Response(403)) as client,
        pytest.raises(gbfs.ForbiddenError),
    ):
        gbfs.fetch_feed(client, "station_status", sleep=sleeps.append)
    assert sleeps == []


def test_fetch_feed_rejects_json_that_is_not_an_object() -> None:
    with (
        _client(lambda request: httpx.Response(200, json=[1, 2])) as client,
        pytest.raises(gbfs.InvalidFeedError),
    ):
        gbfs.fetch_feed(client, "station_status")


def test_parse_station_status_reads_documented_fields() -> None:
    document = _status_document([_raw_status(2, mechanical=4, ebike=1, docks=7), _raw_status(1)])
    snapshot = gbfs.parse_station_status(document, min_stations=1)
    assert snapshot.feed_updated_at == datetime.fromtimestamp(1791202552, tz=UTC)
    assert snapshot.stations[0] == gbfs.StationStatus(
        station_id=2,
        mechanical=4,
        ebike=1,
        docks=7,
        is_installed=True,
        is_renting=True,
        is_returning=False,
        last_reported=1791202000,
    )
    assert len(snapshot.stations) == 2


def test_parse_station_status_counts_a_missing_bike_type_as_zero() -> None:
    raw = _raw_status(1)
    raw["num_bikes_available_types"] = [{"mechanical": 3}]
    snapshot = gbfs.parse_station_status(_status_document([raw]), min_stations=1)
    assert snapshot.stations[0].ebike == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("station_id", "12"),
        ("num_docks_available", None),
        ("is_renting", 2),
        ("last_reported", True),
    ],
)
def test_parse_station_status_rejects_a_bad_field(field: str, value: object) -> None:
    raw = _raw_status(1)
    raw[field] = value
    with pytest.raises(gbfs.InvalidFeedError):
        gbfs.parse_station_status(_status_document([raw]), min_stations=1)


def test_parse_station_status_rejects_a_missing_field() -> None:
    raw = _raw_status(1)
    del raw["num_docks_available"]
    with pytest.raises(gbfs.InvalidFeedError):
        gbfs.parse_station_status(_status_document([raw]), min_stations=1)


def test_parse_station_status_rejects_a_duplicate_station() -> None:
    document = _status_document([_raw_status(1), _raw_status(1)])
    with pytest.raises(gbfs.InvalidFeedError, match="more than once"):
        gbfs.parse_station_status(document, min_stations=1)


def test_parse_station_status_requires_1000_stations_by_default() -> None:
    document = _status_document([_raw_status(station_id) for station_id in range(5)])
    with pytest.raises(gbfs.InvalidFeedError, match="only 5 stations"):
        gbfs.parse_station_status(document)


def test_parse_recorded_station_status() -> None:
    document = json.loads((FIXTURES / "station_status.json").read_text(encoding="utf-8"))
    snapshot = gbfs.parse_station_status(document, min_stations=20)
    first = document["data"]["stations"][0]
    assert len(snapshot.stations) == 20
    assert snapshot.stations[0].station_id == first["station_id"]
    assert snapshot.stations[0].docks == first["num_docks_available"]


def test_parse_recorded_station_information() -> None:
    document = json.loads((FIXTURES / "station_information.json").read_text(encoding="utf-8"))
    stations = gbfs.parse_station_information(document, min_stations=20)
    first = document["data"]["stations"][0]
    assert len(stations) == 20
    assert stations[0] == gbfs.StationInfo(
        station_id=first["station_id"],
        station_code=first["stationCode"],
        name=first["name"],
        lat=first["lat"],
        lon=first["lon"],
        capacity=first["capacity"],
    )
