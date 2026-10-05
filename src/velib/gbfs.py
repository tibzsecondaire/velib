"""Client for the Vélib' Métropole GBFS feeds."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

FEEDS_BASE_URL = "https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole"
USER_AGENT = "velib-data-collector (+https://github.com/tibzsecondaire/velib)"
TIMEOUT_SECONDS = 20.0
RETRY_DELAYS_SECONDS = (2.0, 4.0)
MIN_STATIONS = 1000


class GbfsError(Exception):
    """A GBFS feed could not be downloaded or read."""


class ForbiddenError(GbfsError):
    """The API answered 403, usually because it rejects the User-Agent."""


class InvalidFeedError(GbfsError):
    """A feed does not have the expected content."""


class _RetryableError(Exception):
    """A failure worth retrying: network error, timeout or 5xx answer."""


@dataclass(frozen=True)
class StationStatus:
    """Live status of one station."""

    station_id: int
    mechanical: int
    ebike: int
    docks: int
    is_installed: bool
    is_renting: bool
    is_returning: bool
    last_reported: int


@dataclass(frozen=True)
class StatusSnapshot:
    """Every station status from one download of the station_status feed."""

    feed_updated_at: datetime
    stations: list[StationStatus]


@dataclass(frozen=True)
class StationInfo:
    """Static information about one station."""

    station_id: int
    station_code: str
    name: str
    lat: float
    lon: float
    capacity: int


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Builds an HTTP client with an explicit User-Agent and a timeout.

    Args:
        transport: Transport to use instead of the network, for tests.

    Returns:
        A client to close after use.
    """
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, transport=transport
    )


def fetch_feed(
    client: httpx.Client, feed: str, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """Downloads one GBFS feed and returns its JSON document.

    Network errors, timeouts and 5xx answers are retried after each delay of
    RETRY_DELAYS_SECONDS. A 403 answer fails at once.

    Args:
        client: HTTP client, usually from make_client.
        feed: Feed name, for example "station_status".
        sleep: Function that waits between attempts.

    Returns:
        The decoded JSON object.

    Raises:
        ForbiddenError: The API answered 403.
        InvalidFeedError: The answer is not a JSON object.
        GbfsError: Every attempt failed, or the API answered another 4xx code.
    """
    url = f"{FEEDS_BASE_URL}/{feed}.json"
    delays = list(RETRY_DELAYS_SECONDS)
    while True:
        try:
            return _get_json(client, url)
        except _RetryableError as error:
            if not delays:
                raise GbfsError(f"could not download {url}: {error}") from error
            sleep(delays.pop(0))


def parse_station_status(
    document: dict[str, Any], min_stations: int = MIN_STATIONS
) -> StatusSnapshot:
    """Checks a station_status document and converts it.

    Args:
        document: Decoded station_status feed.
        min_stations: Smallest acceptable number of stations.

    Returns:
        The snapshot, with stations in feed order.

    Raises:
        InvalidFeedError: A field is missing or has the wrong type, a station
            appears twice, or there are too few stations.
    """
    try:
        feed_updated_at = datetime.fromtimestamp(_as_int(document["lastUpdatedOther"]), tz=UTC)
        stations = [_parse_status(raw) for raw in document["data"]["stations"]]
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidFeedError(f"unexpected station_status content: {error!r}") from error
    _check_station_ids([station.station_id for station in stations], min_stations)
    return StatusSnapshot(feed_updated_at=feed_updated_at, stations=stations)


def parse_station_information(
    document: dict[str, Any], min_stations: int = MIN_STATIONS
) -> list[StationInfo]:
    """Checks a station_information document and converts it.

    Args:
        document: Decoded station_information feed.
        min_stations: Smallest acceptable number of stations.

    Returns:
        The stations, in feed order.

    Raises:
        InvalidFeedError: A field is missing or has the wrong type, a station
            appears twice, or there are too few stations.
    """
    try:
        stations = [
            StationInfo(
                station_id=_as_int(raw["station_id"]),
                station_code=str(raw["stationCode"]),
                name=str(raw["name"]),
                lat=float(raw["lat"]),
                lon=float(raw["lon"]),
                capacity=_as_int(raw["capacity"]),
            )
            for raw in document["data"]["stations"]
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidFeedError(f"unexpected station_information content: {error!r}") from error
    _check_station_ids([station.station_id for station in stations], min_stations)
    return stations


def fetch_station_status(client: httpx.Client) -> StatusSnapshot:
    """Downloads and checks the station_status feed."""
    return parse_station_status(fetch_feed(client, "station_status"))


def fetch_station_information(client: httpx.Client) -> list[StationInfo]:
    """Downloads and checks the station_information feed."""
    return parse_station_information(fetch_feed(client, "station_information"))


def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:
    try:
        response = client.get(url)
    except httpx.TransportError as error:
        raise _RetryableError(f"{type(error).__name__}: {error}") from error
    if response.status_code == httpx.codes.FORBIDDEN:
        raise ForbiddenError(f"{url} answered 403: the User-Agent may be rejected")
    if response.is_server_error:
        raise _RetryableError(f"HTTP {response.status_code}")
    if response.is_error:
        raise GbfsError(f"{url} answered HTTP {response.status_code}")
    try:
        document = response.json()
    except ValueError as error:
        raise InvalidFeedError(f"{url} did not return valid JSON") from error
    if not isinstance(document, dict):
        raise InvalidFeedError(f"{url} did not return a JSON object")
    return document


def _parse_status(raw: dict[str, Any]) -> StationStatus:
    bike_types: dict[str, int] = {}
    for entry in raw["num_bikes_available_types"]:
        for kind, count in entry.items():
            bike_types[kind] = _as_int(count)
    return StationStatus(
        station_id=_as_int(raw["station_id"]),
        mechanical=bike_types.get("mechanical", 0),
        ebike=bike_types.get("ebike", 0),
        docks=_as_int(raw["num_docks_available"]),
        is_installed=_as_flag(raw["is_installed"]),
        is_renting=_as_flag(raw["is_renting"]),
        is_returning=_as_flag(raw["is_returning"]),
        last_reported=_as_int(raw["last_reported"]),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an integer, got {value!r}")
    return value


def _as_flag(value: object) -> bool:
    number = _as_int(value)
    if number not in (0, 1):
        raise ValueError(f"expected 0 or 1, got {number!r}")
    return number == 1


def _check_station_ids(station_ids: list[int], min_stations: int) -> None:
    if len(set(station_ids)) != len(station_ids):
        raise InvalidFeedError("a station_id appears more than once")
    if len(station_ids) < min_stations:
        raise InvalidFeedError(
            f"only {len(station_ids)} stations, expected at least {min_stations}"
        )
