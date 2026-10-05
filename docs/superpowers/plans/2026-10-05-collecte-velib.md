# Plan d'implémentation de la collecte Vélib'

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relever l'état des 1 519 stations Vélib' toutes les 5 minutes dans le dépôt public `velib-data`, regrouper
chaque journée en Parquet, et tracer la heatmap d'une journée.

**Architecture:** Le code vit dans le package `velib` (dépôt `tibzsecondaire/velib`). Les tâches GitHub Actions de
`tibzsecondaire/velib-data` récupèrent ce code à un tag (`collector-v1`, puis `collector-v2`) et lancent
`velib-collect snapshot` toutes les 5 minutes et `velib-collect compact` chaque nuit. L'historique git de `raw/` contient
chaque relevé ; le regroupement le relit pour écrire `daily/AAAA-MM-JJ.parquet`.

**Tech Stack:** Python 3.12, uv, httpx 0.28, polars 1.44, plotly 7 (groupe `analysis`), pytest, GitHub Actions
(`actions/checkout@v7`, `astral-sh/setup-uv@v10.2.0`).

**Spec:** `docs/superpowers/specs/2026-10-05-collecte-velib-design.md`

## Global Constraints

- Python `==3.12.*`, gestion des dépendances avec uv uniquement (`uv add`, `uv sync`, `uv run`)
- chaque module commence par `from __future__ import annotations`, docstrings Google, lignes de 100 caractères au plus
- `polars` (jamais pandas), `pathlib` (jamais `os.path`), `logging` (jamais `print`, interdit par ruff T20)
- aucun appel réseau dans les tests
- API : base `https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole`, User-Agent
  `velib-data-collector (+https://github.com/tibzsecondaire/velib)`, délai maximal 20 secondes, 3 essais au total
  espacés de 2 puis 4 secondes, échec immédiat sur 403, au moins 1 000 stations
- `raw/station_status.csv` : colonnes exactes `station_id,mechanical,ebike,docks,is_installed,is_renting,is_returning,last_reported`,
  une ligne par station, triée par `station_id`, fins de ligne `\n`
- message de commit d'un relevé : `snapshot fetched_at=AAAA-MM-JJTHH:MM:SSZ feed_updated_at=AAAA-MM-JJTHH:MM:SSZ`
- `raw/snapshot_meta.json` : `{"fetched_at": "...Z", "feed_updated_at": "...Z", "stations": N}` suivi de `\n`
- Parquet quotidien : heures en `Datetime("us", "UTC")`, compteurs en `Int16`, statuts en `Boolean`, compression zstd,
  jour = date UTC de `fetched_at`
- `daily/index.csv` : colonnes `date,snapshots,first_fetch,last_fetch,max_gap_minutes,stations,rows`
- tâches : `cron: "2-59/5 * * * *"` (collecte, 5 minutes max) et `cron: "15 0 * * *"` (regroupement, 15 minutes max),
  groupe de `concurrency` `velib-data` sans annulation, permission `contents: write` seule, commits signés
  `github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>`, push réessayé 3 fois après `git pull --rebase`
- identité : commits `tibzsecondaire <338156179+tibzsecondaire@users.noreply.github.com>` (déjà configuré dans le dépôt
  local `velib`) ; `gh` seulement via `GH_CONFIG_DIR="$HOME/.config/gh-tibzsecondaire"`, après avoir vérifié que
  `gh api user --jq .login` renvoie `tibzsecondaire` ; push par l'alias SSH `github-tibz`
- avant chaque push, chercher dans l'historique à pousser les motifs du fichier local
  `.git/info/private-identifiers`, que git ne committe jamais (voir la tâche 3, étape 3)
- aucun chemin local, aucun nom de compte ou d'adresse personnelle dans les fichiers du dépôt
- messages de commit au format Conventional Commits, terminés par la ligne
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
- `velib-data` : README en anglais, licence ODbL 1.0 (texte SPDX), `.gitignore` contenant `code/`

## Écarts assumés par rapport au design

- `velib.collect` devient un sous-package (`snapshot.py`, `compact.py`, `cli.py`) pour garder des fichiers courts.
  La commande `velib-collect` et ses sous-commandes restent celles du design.
- le calcul de la heatmap vit dans `velib/heatmap.py` pour être testé ; le script ne fait que tracer.
- les tags arrivent en 2 temps pour lancer la collecte au plus vite : `collector-v1` (relevé seul), puis
  `collector-v2` (avec le regroupement), sur lequel les 2 tâches pointent ensuite.

## Fichiers

Dépôt `velib` (les commandes se lancent depuis sa racine, sauf mention contraire) :

| Fichier | Rôle |
|---|---|
| `pyproject.toml` | dépendances, point d'entrée `velib-collect`, groupe `analysis` |
| `src/velib/gbfs.py` | téléchargement et validation des flux GBFS |
| `src/velib/collect/__init__.py` | sous-package de collecte |
| `src/velib/collect/snapshot.py` | format brut : CSV, `snapshot_meta.json`, message de commit |
| `src/velib/collect/compact.py` | relecture git d'une journée, Parquet, `index.csv`, CSV des stations |
| `src/velib/collect/cli.py` | commande `velib-collect snapshot | compact` |
| `src/velib/dataset.py` | `load_day`, `load_stations` avec cache local |
| `src/velib/heatmap.py` | taux de remplissage par station et par tranche de 15 minutes |
| `scripts/heatmap_day.py` | tracé HTML de la heatmap d'une journée |
| `tests/fixtures/*.json` | vraies réponses de l'API réduites à 20 stations |
| `tests/unit/test_gbfs.py`, `test_snapshot.py`, `test_cli.py`, `test_dataset.py`, `test_heatmap.py` | tests unitaires |
| `tests/integration/test_compact.py` | regroupement sur un vrai dépôt git temporaire |

Dépôt `velib-data` (copie de travail temporaire `$WORK/velib-data`, où `WORK` est un dossier temporaire au choix) : `README.md`, `LICENSE`, `.gitignore`,
`.github/workflows/collect.yml`, `.github/workflows/compact.yml`.

---

## Phase A : lancer la collecte

### Task 1: Client des flux GBFS

**Files:**
- Modify: `pyproject.toml` (dépendance `httpx`)
- Create: `src/velib/gbfs.py`
- Create: `tests/fixtures/station_status.json`, `tests/fixtures/station_information.json`
- Create: `tests/unit/test_gbfs.py`
- Delete: `tests/unit/test_dummy.py`, `tests/integration/test_dummy.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - constantes `FEEDS_BASE_URL: str`, `USER_AGENT: str`, `TIMEOUT_SECONDS = 20.0`,
    `RETRY_DELAYS_SECONDS = (2.0, 4.0)`, `MIN_STATIONS = 1000`
  - exceptions `GbfsError(Exception)`, `ForbiddenError(GbfsError)`, `InvalidFeedError(GbfsError)`
  - dataclasses figées `StationStatus(station_id: int, mechanical: int, ebike: int, docks: int, is_installed: bool,
    is_renting: bool, is_returning: bool, last_reported: int)`, `StatusSnapshot(feed_updated_at: datetime,
    stations: list[StationStatus])`, `StationInfo(station_id: int, station_code: str, name: str, lat: float,
    lon: float, capacity: int)`
  - `make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client`
  - `fetch_feed(client: httpx.Client, feed: str, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]`
  - `parse_station_status(document: dict[str, Any], min_stations: int = MIN_STATIONS) -> StatusSnapshot`
  - `parse_station_information(document: dict[str, Any], min_stations: int = MIN_STATIONS) -> list[StationInfo]`
  - `fetch_station_status(client: httpx.Client) -> StatusSnapshot`
  - `fetch_station_information(client: httpx.Client) -> list[StationInfo]`

- [ ] **Step 1: Créer la branche et ajouter httpx**

```bash
git switch -c feat/collector
uv add httpx
```

Expected: `pyproject.toml` liste `"httpx>=0.28.1"` dans `dependencies`, `uv.lock` est mis à jour.

- [ ] **Step 2: Enregistrer les réponses de l'API dans les fixtures**

```bash
mkdir -p tests/fixtures
uv run python - <<'PY'
import json
from pathlib import Path

import httpx

base = "https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole"
headers = {"User-Agent": "velib-data-collector (+https://github.com/tibzsecondaire/velib)"}
for feed in ("station_status", "station_information"):
    document = httpx.get(f"{base}/{feed}.json", headers=headers, timeout=20).raise_for_status().json()
    stations = sorted(document["data"]["stations"], key=lambda station: station["station_id"])[:20]
    document["data"]["stations"] = stations
    path = Path("tests/fixtures") / f"{feed}.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path, len(stations))
PY
```

Expected: 2 fichiers de 20 stations chacun.

- [ ] **Step 3: Écrire les tests qui échouent**

Créer `tests/unit/test_gbfs.py` :

```python
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
```

Supprimer les tests factices du squelette :

```bash
git rm -q tests/unit/test_dummy.py tests/integration/test_dummy.py
```

- [ ] **Step 4: Vérifier que les tests échouent**

Run: `uv run pytest tests/unit/test_gbfs.py -q`
Expected: FAIL avec `ImportError: cannot import name 'gbfs' from 'velib'`.

- [ ] **Step 5: Écrire `src/velib/gbfs.py`**

```python
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
```

- [ ] **Step 6: Vérifier que les tests passent**

Run: `uv run pytest tests/unit/test_gbfs.py -q`
Expected: PASS (16 tests).

- [ ] **Step 7: Passer les hooks et committer**

```bash
git add pyproject.toml uv.lock src/velib/gbfs.py tests/fixtures tests/unit/test_gbfs.py
uv run pre-commit run --all-files
```

Expected: tous les hooks passent. Si codespell signale des noms de stations en français dans `tests/fixtures/`,
remplacer dans `.pre-commit-config.yaml` la ligne `exclude: ^docs/superpowers/` par
`exclude: ^(docs/superpowers/|tests/fixtures/)`, ajouter ce fichier à l'index, puis relancer la commande.

```bash
git commit -q -m "feat: add gbfs feed client" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2: Relevé brut et commande `velib-collect snapshot`

**Files:**
- Modify: `pyproject.toml` (section `[project.scripts]`)
- Create: `src/velib/collect/__init__.py`, `src/velib/collect/snapshot.py`, `src/velib/collect/cli.py`
- Create: `tests/unit/test_snapshot.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `velib.gbfs.StationStatus`, `StatusSnapshot`, `make_client`, `fetch_station_status`, `GbfsError`
- Produces:
  - `velib.collect.snapshot` : `STATUS_FILENAME = "station_status.csv"`, `META_FILENAME = "snapshot_meta.json"`,
    `STATUS_COLUMNS: tuple[str, ...]`, dataclass figée `SnapshotTimes(fetched_at: datetime, feed_updated_at: datetime)`,
    `format_time(moment: datetime) -> str`, `parse_time(text: str) -> datetime`,
    `format_commit_message(times: SnapshotTimes) -> str`, `parse_commit_message(message: str) -> SnapshotTimes | None`,
    `write_snapshot(snapshot: StatusSnapshot, output_dir: Path, fetched_at: datetime) -> str`
  - `velib.collect.cli.main(argv: Sequence[str] | None = None) -> int`, exposé comme `velib-collect`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/unit/test_snapshot.py` :

```python
"""Tests for the raw snapshot format."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from velib.collect.snapshot import (
    SnapshotTimes,
    format_commit_message,
    format_time,
    parse_commit_message,
    write_snapshot,
)
from velib.gbfs import StationStatus, StatusSnapshot

FEED_UPDATED_AT = datetime(2026, 10, 5, 12, 6, 55, tzinfo=UTC)
FETCHED_AT = datetime(2026, 10, 5, 12, 7, 31, 123456, tzinfo=UTC)


def _station(station_id: int, mechanical: int, ebike: int, docks: int) -> StationStatus:
    return StationStatus(
        station_id=station_id,
        mechanical=mechanical,
        ebike=ebike,
        docks=docks,
        is_installed=True,
        is_renting=True,
        is_returning=False,
        last_reported=1791202000 + station_id,
    )


def test_write_snapshot_writes_a_sorted_csv(tmp_path: Path) -> None:
    snapshot = StatusSnapshot(FEED_UPDATED_AT, [_station(20, 1, 2, 3), _station(10, 4, 5, 6)])
    write_snapshot(snapshot, tmp_path / "raw", FETCHED_AT)
    content = (tmp_path / "raw" / "station_status.csv").read_bytes().decode("utf-8")
    assert content == (
        "station_id,mechanical,ebike,docks,is_installed,is_renting,is_returning,last_reported\n"
        "10,4,5,6,1,1,0,1791202010\n"
        "20,1,2,3,1,1,0,1791202020\n"
    )


def test_write_snapshot_writes_meta_and_returns_the_commit_message(tmp_path: Path) -> None:
    snapshot = StatusSnapshot(FEED_UPDATED_AT, [_station(10, 4, 5, 6)])
    message = write_snapshot(snapshot, tmp_path, FETCHED_AT)
    meta = (tmp_path / "snapshot_meta.json").read_text(encoding="utf-8")
    assert meta.endswith("}\n")
    assert json.loads(meta) == {
        "fetched_at": "2026-10-05T12:07:31Z",
        "feed_updated_at": "2026-10-05T12:06:55Z",
        "stations": 1,
    }
    expected = "snapshot fetched_at=2026-10-05T12:07:31Z feed_updated_at=2026-10-05T12:06:55Z"
    assert message == expected


def test_commit_message_round_trip() -> None:
    times = SnapshotTimes(
        fetched_at=datetime(2026, 10, 5, 12, 7, 31, tzinfo=UTC), feed_updated_at=FEED_UPDATED_AT
    )
    assert parse_commit_message(format_commit_message(times)) == times


def test_parse_commit_message_ignores_other_commits() -> None:
    assert parse_commit_message("compact 2026-10-05") is None
    assert parse_commit_message("build: start collecting station snapshots") is None
    assert parse_commit_message("") is None


def test_format_time_converts_to_utc() -> None:
    summer_time = timezone(timedelta(hours=2))
    moment = datetime(2026, 10, 5, 14, 7, 31, tzinfo=summer_time)
    assert format_time(moment) == "2026-10-05T12:07:31Z"
```

Créer `tests/unit/test_cli.py` :

```python
"""Tests for the velib-collect command line."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from velib import gbfs
from velib.collect import cli
from velib.gbfs import StationStatus, StatusSnapshot

SNAPSHOT = StatusSnapshot(
    feed_updated_at=datetime(2026, 10, 5, 12, 6, 55, tzinfo=UTC),
    stations=[
        StationStatus(
            station_id=10,
            mechanical=4,
            ebike=5,
            docks=6,
            is_installed=True,
            is_renting=True,
            is_returning=False,
            last_reported=1791202010,
        )
    ],
)


def test_snapshot_command_writes_files_and_prints_the_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gbfs, "fetch_station_status", lambda client: SNAPSHOT)
    exit_code = cli.main(["snapshot", "--output-dir", str(tmp_path / "raw")])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("snapshot fetched_at=")
    assert out.endswith(" feed_updated_at=2026-10-05T12:06:55Z\n")
    assert (tmp_path / "raw" / "station_status.csv").is_file()
    assert (tmp_path / "raw" / "snapshot_meta.json").is_file()


def test_snapshot_command_fails_without_writing_on_a_feed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(client: httpx.Client) -> StatusSnapshot:
        raise gbfs.ForbiddenError("403")

    monkeypatch.setattr(gbfs, "fetch_station_status", forbidden)
    exit_code = cli.main(["snapshot", "--output-dir", str(tmp_path / "raw")])
    assert exit_code == 1
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "raw").exists()
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `uv run pytest tests/unit/test_snapshot.py tests/unit/test_cli.py -q`
Expected: FAIL avec `ModuleNotFoundError: No module named 'velib.collect'`.

- [ ] **Step 3: Écrire le sous-package**

Créer `src/velib/collect/__init__.py` :

```python
"""Collection of Vélib' station data for the velib-data repository."""
```

Créer `src/velib/collect/snapshot.py` :

```python
"""Raw snapshot format, shared by the collector and the daily compaction."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from velib.gbfs import StatusSnapshot

STATUS_FILENAME = "station_status.csv"
META_FILENAME = "snapshot_meta.json"
STATUS_COLUMNS = (
    "station_id",
    "mechanical",
    "ebike",
    "docks",
    "is_installed",
    "is_renting",
    "is_returning",
    "last_reported",
)

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MESSAGE_PATTERN = re.compile(r"^snapshot fetched_at=(\S+) feed_updated_at=(\S+)$")


@dataclass(frozen=True)
class SnapshotTimes:
    """When a snapshot was fetched, and when the feed was last updated."""

    fetched_at: datetime
    feed_updated_at: datetime


def format_time(moment: datetime) -> str:
    """Formats a timezone-aware datetime as ISO 8601 UTC, to the second."""
    return moment.astimezone(UTC).strftime(_TIME_FORMAT)


def parse_time(text: str) -> datetime:
    """Parses a time written by format_time."""
    return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=UTC)


def format_commit_message(times: SnapshotTimes) -> str:
    """Builds the commit message that records the times of a snapshot."""
    fetched = format_time(times.fetched_at)
    updated = format_time(times.feed_updated_at)
    return f"snapshot fetched_at={fetched} feed_updated_at={updated}"


def parse_commit_message(message: str) -> SnapshotTimes | None:
    """Reads the times recorded in a snapshot commit message.

    Args:
        message: Full commit message.

    Returns:
        The times, or None when the first line is not a snapshot message.
    """
    lines = message.splitlines()
    match = _MESSAGE_PATTERN.match(lines[0]) if lines else None
    if match is None:
        return None
    return SnapshotTimes(fetched_at=parse_time(match[1]), feed_updated_at=parse_time(match[2]))


def write_snapshot(snapshot: StatusSnapshot, output_dir: Path, fetched_at: datetime) -> str:
    """Writes station_status.csv and snapshot_meta.json for one snapshot.

    Args:
        snapshot: Checked station statuses.
        output_dir: Directory for both files, created when missing.
        fetched_at: Timezone-aware time of the download.

    Returns:
        The commit message for this snapshot.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stations = sorted(snapshot.stations, key=lambda station: station.station_id)
    with (output_dir / STATUS_FILENAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(STATUS_COLUMNS)
        for station in stations:
            writer.writerow(
                (
                    station.station_id,
                    station.mechanical,
                    station.ebike,
                    station.docks,
                    int(station.is_installed),
                    int(station.is_renting),
                    int(station.is_returning),
                    station.last_reported,
                )
            )
    times = SnapshotTimes(fetched_at=fetched_at, feed_updated_at=snapshot.feed_updated_at)
    meta = {
        "fetched_at": format_time(times.fetched_at),
        "feed_updated_at": format_time(times.feed_updated_at),
        "stations": len(stations),
    }
    (output_dir / META_FILENAME).write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return format_commit_message(times)
```

Créer `src/velib/collect/cli.py` :

```python
"""Command line entry point: velib-collect snapshot."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from velib import gbfs
from velib.collect.snapshot import write_snapshot

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-collect command line.

    Args:
        argv: Arguments without the program name. Defaults to sys.argv[1:].

    Returns:
        The process exit code.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    args = _build_parser().parse_args(argv)
    try:
        return _snapshot(args.output_dir)
    except gbfs.GbfsError as error:
        logger.error("%s", error)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="velib-collect", description="Collect Vélib' station data for velib-data."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="Fetch station_status and write the raw files.")
    snapshot.add_argument(
        "--output-dir", type=Path, required=True, help="Directory that receives the raw files."
    )
    return parser


def _snapshot(output_dir: Path) -> int:
    with gbfs.make_client() as client:
        snapshot = gbfs.fetch_station_status(client)
    fetched_at = datetime.now(UTC)
    message = write_snapshot(snapshot, output_dir, fetched_at)
    logger.info("wrote %d stations to %s", len(snapshot.stations), output_dir)
    sys.stdout.write(message + "\n")
    return 0
```

Ajouter à `pyproject.toml`, juste après la liste `dependencies` de `[project]` :

```toml
[project.scripts]
velib-collect = "velib.collect.cli:main"
```

- [ ] **Step 4: Vérifier que les tests passent**

```bash
uv sync --all-groups
uv run pytest -q
```

Expected: PASS (23 tests), et `uv run velib-collect --help` affiche la sous-commande `snapshot`.

- [ ] **Step 5: Essayer un vrai relevé, hors du dépôt**

```bash
uv run velib-collect snapshot --output-dir "${TMPDIR:-/tmp}/velib-raw-try"
```

Expected: la sortie standard est une seule ligne `snapshot fetched_at=… feed_updated_at=…` ; le CSV contient environ
1 519 lignes plus l'en-tête.

- [ ] **Step 6: Passer les hooks et committer**

```bash
git add pyproject.toml uv.lock src/velib/collect tests/unit/test_snapshot.py tests/unit/test_cli.py
uv run pre-commit run --all-files
git commit -q -m "feat: add velib-collect snapshot command" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 3: Fusionner, poser le tag `collector-v1` et publier

**Files:**
- Modify: `README.md` (section sur la collecte)

**Interfaces:**
- Consumes: branche `feat/collector` (tâches 1 et 2)
- Produces: `main` et le tag `collector-v1` sur `git@github-tibz:tibzsecondaire/velib.git`

- [ ] **Step 1: Documenter la collecte dans le README**

Dans `README.md`, juste avant la section `## Getting started`, ajouter :

```markdown
## Data collection

`velib-collect snapshot --output-dir raw` downloads the live status of every station and writes
`raw/station_status.csv` and `raw/snapshot_meta.json`. GitHub Actions runs it every 5 minutes in
[tibzsecondaire/velib-data](https://github.com/tibzsecondaire/velib-data), which publishes the history.
```

```bash
git add README.md
git commit -q -m "docs: describe the data collection" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Fusionner et tout revérifier**

```bash
git switch main
git merge --ff-only feat/collector
make lint
make test
```

Expected: tous les hooks passent, tous les tests passent.

- [ ] **Step 3: Chercher les identifiants personnels avant de pousser**

```bash
git log --format='%an <%ae> | %cn <%ce>' origin/main..main | sort -u
git log -p origin/main..main | grep -ciEf .git/info/private-identifiers
```

Expected: une seule identité, `tibzsecondaire <338156179+tibzsecondaire@users.noreply.github.com>`, et le compteur à `0`.
S'il n'est pas à 0, s'arrêter et corriger avant tout push.

- [ ] **Step 4: Pousser main et le tag**

```bash
git push origin main
git tag -a collector-v1 -m "collector: station snapshots"
git push origin collector-v1
```

Expected: `git ls-remote --tags origin` liste `refs/tags/collector-v1`.

### Task 4: Démarrer `velib-data` et la collecte

**Files (dépôt `velib-data`):**
- Create: `README.md`, `LICENSE`, `.gitignore`, `.github/workflows/collect.yml`

**Interfaces:**
- Consumes: le tag `collector-v1` et la commande `velib-collect snapshot --output-dir raw`
- Produces: un commit `snapshot …` toutes les 5 minutes sur `tibzsecondaire/velib-data`

- [ ] **Step 1: Cloner le dépôt vide avec l'identité du projet**

```bash
WORK="${WORK:-${TMPDIR:-/tmp}/velib-work}"
git clone -q git@github-tibz:tibzsecondaire/velib-data.git "$WORK/velib-data"
cd "$WORK/velib-data"
git symbolic-ref HEAD refs/heads/main
git config user.name tibzsecondaire
git config user.email 338156179+tibzsecondaire@users.noreply.github.com
mkdir -p .github/workflows
printf 'code/\n' > .gitignore
curl -sSf -o LICENSE https://raw.githubusercontent.com/spdx/license-list-data/main/text/ODbL-1.0.txt
head -3 LICENSE
```

Expected: les premières lignes de `LICENSE` contiennent « Open Database License ».

- [ ] **Step 2: Écrire `README.md`**

````markdown
# velib-data

History of the availability of the Vélib' Métropole bike-sharing stations (Paris), recorded every
5 minutes from the official GBFS open data feeds.

The collection code lives in [tibzsecondaire/velib](https://github.com/tibzsecondaire/velib).

## Source and license

Contains data from the Vélib' Métropole GBFS feeds
(<https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/gbfs.json>), published as
open data. The equivalent dataset published by the City of Paris on data.gouv.fr is under the ODbL.
This derived database is made available under the [Open Database License (ODbL) 1.0](./LICENSE).

## Files

| Path | Content | Updated |
|---|---|---|
| `raw/station_status.csv` | latest snapshot, one row per station | every 5 minutes |
| `raw/snapshot_meta.json` | time of the latest snapshot and number of stations | every 5 minutes |
| `daily/YYYY-MM-DD.parquet` | one UTC day, one row per station and per snapshot | every night |
| `daily/index.csv` | one row per day: snapshots, first and last fetch, largest gap | every night |
| `stations/station_information.csv` | code, name, latitude, longitude and capacity of each station | every night |

All times are UTC. `raw/station_status.csv` has the columns `station_id`, `mechanical` and `ebike`
(available bikes of each type), `docks` (free docks), `is_installed`, `is_renting`, `is_returning`
(0 or 1) and `last_reported` (Unix time of the station's last report).

Every snapshot is a commit: its message records `fetched_at`, the time of the download, and
`feed_updated_at`, the time the feed was generated. The daily Parquet files rebuild each day from
this history.

## Caveats

- GitHub can delay or skip scheduled runs, so snapshots are not exactly 5 minutes apart.
  `daily/index.csv` gives the largest gap of each day.
- Some stations have not reported for a long time. Check `last_reported`.

## Loading a day with polars

```python
import polars as pl

url = "https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/daily/2026-10-06.parquet"
day = pl.read_parquet(url)
```
````

- [ ] **Step 3: Écrire `.github/workflows/collect.yml`**

```yaml
name: collect

on:
  schedule:
    - cron: "2-59/5 * * * *"
  workflow_dispatch:

concurrency:
  group: velib-data
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  snapshot:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check out velib-data
        uses: actions/checkout@v7
        with:
          fetch-depth: 1

      - name: Check out the velib code
        uses: actions/checkout@v7
        with:
          repository: tibzsecondaire/velib
          ref: collector-v1
          path: code
          persist-credentials: false

      - name: Install uv
        uses: astral-sh/setup-uv@v10.2.0
        with:
          enable-cache: true
          cache-dependency-glob: code/uv.lock

      - name: Take a snapshot
        id: snapshot
        run: |
          message="$(uv run --project code --no-dev --frozen velib-collect snapshot --output-dir raw)"
          echo "message=${message}" >> "$GITHUB_OUTPUT"

      - name: Commit and push
        env:
          MESSAGE: ${{ steps.snapshot.outputs.message }}
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add raw
          git commit --quiet -m "$MESSAGE"
          for attempt in 1 2 3; do
            if git push --quiet; then
              exit 0
            fi
            echo "push rejected (attempt ${attempt}), rebasing"
            git pull --rebase --quiet
          done
          exit 1
```

- [ ] **Step 4: Committer et pousser**

```bash
cd "$WORK/velib-data"
git add -A
git commit -q -m "build: collect station snapshots every 5 minutes" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push -q -u origin main
```

- [ ] **Step 5: Lancer un premier relevé à la main et le vérifier**

```bash
export GH_CONFIG_DIR="$HOME/.config/gh-tibzsecondaire"
test "$(gh api user --jq .login)" = "tibzsecondaire"
gh workflow run collect.yml --repo tibzsecondaire/velib-data --ref main
gh run list --repo tibzsecondaire/velib-data --workflow collect.yml --event workflow_dispatch --limit 1
run_id="$(gh run list --repo tibzsecondaire/velib-data --workflow collect.yml --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$run_id" --repo tibzsecondaire/velib-data --exit-status
gh api repos/tibzsecondaire/velib-data/commits --jq '.[0].commit.message'
curl -s https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/raw/snapshot_meta.json
```

Expected: le run `workflow_dispatch` apparaît (sinon, relancer `gh run list` quelques secondes plus tard), puis se termine en succès ; le dernier commit commence par `snapshot fetched_at=` ; le JSON indique
environ 1 519 stations. Si `gh workflow run` répond que le workflow est introuvable, attendre quelques secondes que
GitHub l'enregistre et relancer.

- [ ] **Step 6: Vérifier que les relevés planifiés tournent**

Environ 30 minutes plus tard :

```bash
gh run list --repo tibzsecondaire/velib-data --workflow collect.yml --event schedule --limit 5
```

Expected: au moins un run `schedule` réussi.

## Phase B : regroupement quotidien

### Task 5: Regroupement d'une journée et commande `velib-collect compact`

**Files:**
- Modify: `pyproject.toml` (dépendance `polars`)
- Create: `src/velib/collect/compact.py`
- Modify: `src/velib/collect/cli.py` (sous-commande `compact`)
- Create: `tests/integration/test_compact.py`

**Interfaces:**
- Consumes: `velib.collect.snapshot.META_FILENAME`, `STATUS_FILENAME`, `SnapshotTimes`, `format_time`,
  `parse_commit_message`, `write_snapshot` ; `velib.gbfs.StationInfo`, `fetch_station_information`
- Produces:
  - `velib.collect.compact` : `STATIONS_PATH = Path("stations") / "station_information.csv"`,
    `INDEX_COLUMNS: tuple[str, ...]`, `CompactionError(Exception)`, dataclass figée `DaySummary(day: date,
    snapshots: int, first_fetch: datetime, last_fetch: datetime, max_gap_minutes: float, stations: int, rows: int)`,
    `list_snapshot_commits(repo: Path, day: date) -> list[tuple[str, SnapshotTimes]]`,
    `load_day_frame(repo: Path, commits: list[tuple[str, SnapshotTimes]]) -> pl.DataFrame`,
    `summarize(day: date, frame: pl.DataFrame) -> DaySummary`, `update_index(index_path: Path, summary: DaySummary) -> None`,
    `compact_day(repo: Path, day: date) -> DaySummary`,
    `write_station_information(stations: list[StationInfo], path: Path) -> None`
  - `velib-collect compact --day AAAA-MM-JJ --repo CHEMIN` (jour par défaut : la veille en UTC, dépôt par défaut : `.`)

- [ ] **Step 1: Ajouter polars**

```bash
git switch -c feat/compaction
uv add polars
```

Expected: `"polars>=1.44.2"` dans `dependencies`.

- [ ] **Step 2: Écrire les tests qui échouent**

Créer `tests/integration/test_compact.py` :

```python
"""Tests for the daily compaction, on a real temporary git repository."""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from velib import gbfs
from velib.collect import cli
from velib.collect.compact import CompactionError, compact_day
from velib.collect.snapshot import write_snapshot
from velib.gbfs import StationInfo, StationStatus, StatusSnapshot

pytestmark = pytest.mark.integration

DAY = date(2026, 10, 5)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def _snapshot(mechanical: int) -> StatusSnapshot:
    stations = [
        StationStatus(
            station_id=station_id,
            mechanical=mechanical,
            ebike=1,
            docks=10,
            is_installed=True,
            is_renting=True,
            is_returning=True,
            last_reported=1791200000,
        )
        for station_id in (2, 1)
    ]
    updated = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    return StatusSnapshot(feed_updated_at=updated, stations=stations)


def _record(repo: Path, fetched_at: datetime, mechanical: int) -> None:
    message = write_snapshot(_snapshot(mechanical), repo / "raw", fetched_at)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "velib-data"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("velib-data\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "build: start the repository")
    _record(path, datetime(2026, 10, 4, 23, 58, tzinfo=UTC), mechanical=1)
    _record(path, datetime(2026, 10, 5, 0, 3, tzinfo=UTC), mechanical=2)
    _record(path, datetime(2026, 10, 5, 0, 13, tzinfo=UTC), mechanical=3)
    _git(path, "commit", "-q", "--allow-empty", "-m", "compact 2026-10-04")
    return path


def test_compact_day_keeps_only_the_snapshots_of_that_utc_day(repo: Path) -> None:
    summary = compact_day(repo, DAY)
    frame = pl.read_parquet(repo / "daily" / "2026-10-05.parquet")
    assert summary.snapshots == 2
    assert frame.height == 4
    assert frame.get_column("station_id").to_list() == [1, 1, 2, 2]
    assert frame.get_column("mechanical").to_list() == [2, 3, 2, 3]


def test_compact_day_writes_typed_columns(repo: Path) -> None:
    compact_day(repo, DAY)
    frame = pl.read_parquet(repo / "daily" / "2026-10-05.parquet")
    assert frame.schema == pl.Schema(
        {
            "fetched_at": pl.Datetime("us", "UTC"),
            "feed_updated_at": pl.Datetime("us", "UTC"),
            "station_id": pl.Int64(),
            "mechanical": pl.Int16(),
            "ebike": pl.Int16(),
            "docks": pl.Int16(),
            "is_installed": pl.Boolean(),
            "is_renting": pl.Boolean(),
            "is_returning": pl.Boolean(),
            "last_reported": pl.Datetime("us", "UTC"),
        }
    )
    assert frame.get_column("fetched_at").min() == datetime(2026, 10, 5, 0, 3, tzinfo=UTC)
    assert frame.get_column("last_reported").max() == datetime.fromtimestamp(1791200000, tz=UTC)


def test_compact_day_writes_the_index_and_replaces_its_line_on_rerun(repo: Path) -> None:
    compact_day(repo, DAY)
    compact_day(repo, DAY)
    lines = (repo / "daily" / "index.csv").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "date,snapshots,first_fetch,last_fetch,max_gap_minutes,stations,rows",
        "2026-10-05,2,2026-10-05T00:03:00Z,2026-10-05T00:13:00Z,10.0,2,4",
    ]


def test_compact_day_without_snapshot_fails(repo: Path) -> None:
    with pytest.raises(CompactionError, match="2026-10-07"):
        compact_day(repo, date(2026, 10, 7))


def test_compact_command_also_writes_station_information(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stations = [
        StationInfo(
            station_id=2,
            station_code="16107",
            name="Benjamin Godard - Victor Hugo",
            lat=48.865983,
            lon=2.275725,
            capacity=35,
        ),
        StationInfo(
            station_id=1,
            station_code="00001",
            name="Test, with comma",
            lat=48.85,
            lon=2.35,
            capacity=20,
        ),
    ]

    def fake_information(client: httpx.Client) -> list[StationInfo]:
        return stations

    monkeypatch.setattr(gbfs, "fetch_station_information", fake_information)
    exit_code = cli.main(["compact", "--day", "2026-10-05", "--repo", str(repo)])
    assert exit_code == 0
    assert (repo / "daily" / "2026-10-05.parquet").is_file()
    lines = (repo / "stations" / "station_information.csv").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "station_id,station_code,name,lat,lon,capacity",
        '1,00001,"Test, with comma",48.85,2.35,20',
        "2,16107,Benjamin Godard - Victor Hugo,48.865983,2.275725,35",
    ]


def test_compact_command_reports_a_day_without_snapshot(repo: Path) -> None:
    assert cli.main(["compact", "--day", "2026-10-07", "--repo", str(repo)]) == 1
```

- [ ] **Step 3: Vérifier que les tests échouent**

Run: `uv run pytest tests/integration/test_compact.py -q`
Expected: FAIL avec `ModuleNotFoundError: No module named 'velib.collect.compact'`.

- [ ] **Step 4: Écrire `src/velib/collect/compact.py`**

```python
"""Daily compaction: replays the snapshot commits of one UTC day into Parquet."""

from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from velib.collect.snapshot import (
    META_FILENAME,
    STATUS_FILENAME,
    SnapshotTimes,
    format_time,
    parse_commit_message,
)
from velib.gbfs import StationInfo

RAW_DIR = "raw"
DAILY_DIR = "daily"
INDEX_FILENAME = "index.csv"
STATIONS_PATH = Path("stations") / "station_information.csv"
INDEX_COLUMNS = (
    "date",
    "snapshots",
    "first_fetch",
    "last_fetch",
    "max_gap_minutes",
    "stations",
    "rows",
)
STATION_COLUMNS = ("station_id", "station_code", "name", "lat", "lon", "capacity")

_STATUS_SCHEMA = {
    "station_id": pl.Int64,
    "mechanical": pl.Int16,
    "ebike": pl.Int16,
    "docks": pl.Int16,
    "is_installed": pl.Int8,
    "is_renting": pl.Int8,
    "is_returning": pl.Int8,
    "last_reported": pl.Int64,
}


class CompactionError(Exception):
    """A day cannot be compacted, for example because it has no snapshot."""


@dataclass(frozen=True)
class DaySummary:
    """One line of daily/index.csv."""

    day: date
    snapshots: int
    first_fetch: datetime
    last_fetch: datetime
    max_gap_minutes: float
    stations: int
    rows: int


def list_snapshot_commits(repo: Path, day: date) -> list[tuple[str, SnapshotTimes]]:
    """Lists the snapshot commits fetched during one UTC day.

    Args:
        repo: Path to the velib-data checkout.
        day: UTC day.

    Returns:
        Pairs of commit hash and snapshot times, oldest first.
    """
    output = _git(repo, "log", "--format=%H%x1f%s", "--", f"{RAW_DIR}/{META_FILENAME}")
    commits = []
    for line in output.splitlines():
        sha, _, subject = line.partition("\x1f")
        times = parse_commit_message(subject)
        if times is not None and times.fetched_at.date() == day:
            commits.append((sha, times))
    return sorted(commits, key=lambda commit: commit[1].fetched_at)


def load_day_frame(repo: Path, commits: list[tuple[str, SnapshotTimes]]) -> pl.DataFrame:
    """Reads the raw CSV of each commit and stacks them into one typed frame.

    Args:
        repo: Path to the velib-data checkout.
        commits: Output of list_snapshot_commits.

    Returns:
        One row per station and per snapshot, sorted by station then time.
    """
    frames = []
    for sha, times in commits:
        content = _git(repo, "show", f"{sha}:{RAW_DIR}/{STATUS_FILENAME}")
        frame = pl.read_csv(io.StringIO(content), schema_overrides=_STATUS_SCHEMA)
        frames.append(
            frame.with_columns(
                pl.lit(times.fetched_at).alias("fetched_at"),
                pl.lit(times.feed_updated_at).alias("feed_updated_at"),
            )
        )
    return (
        pl.concat(frames)
        .select(
            "fetched_at",
            "feed_updated_at",
            "station_id",
            "mechanical",
            "ebike",
            "docks",
            pl.col("is_installed").cast(pl.Boolean),
            pl.col("is_renting").cast(pl.Boolean),
            pl.col("is_returning").cast(pl.Boolean),
            pl.from_epoch("last_reported", time_unit="s").dt.replace_time_zone("UTC"),
        )
        .sort("station_id", "fetched_at")
    )


def summarize(day: date, frame: pl.DataFrame) -> DaySummary:
    """Computes the index line of a compacted day."""
    fetches = frame.get_column("fetched_at").unique().sort()
    largest_gap = fetches.diff().max()
    max_gap_minutes = (
        round(largest_gap.total_seconds() / 60, 1) if isinstance(largest_gap, timedelta) else 0.0
    )
    return DaySummary(
        day=day,
        snapshots=fetches.len(),
        first_fetch=fetches[0],
        last_fetch=fetches[-1],
        max_gap_minutes=max_gap_minutes,
        stations=frame.get_column("station_id").n_unique(),
        rows=frame.height,
    )


def update_index(index_path: Path, summary: DaySummary) -> None:
    """Adds or replaces the line of a day in daily/index.csv, sorted by date."""
    lines: dict[str, list[str]] = {}
    if index_path.exists():
        with index_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                lines[row["date"]] = [row[column] for column in INDEX_COLUMNS]
    lines[summary.day.isoformat()] = [
        summary.day.isoformat(),
        str(summary.snapshots),
        format_time(summary.first_fetch),
        format_time(summary.last_fetch),
        f"{summary.max_gap_minutes:.1f}",
        str(summary.stations),
        str(summary.rows),
    ]
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(INDEX_COLUMNS)
        for key in sorted(lines):
            writer.writerow(lines[key])


def compact_day(repo: Path, day: date) -> DaySummary:
    """Writes daily/<day>.parquet and updates daily/index.csv for one UTC day.

    Args:
        repo: Path to the velib-data checkout, with the history of the day.
        day: UTC day to compact.

    Returns:
        The index line of the day.

    Raises:
        CompactionError: No snapshot commit was found for the day.
    """
    commits = list_snapshot_commits(repo, day)
    if not commits:
        raise CompactionError(f"no snapshot commit found for {day.isoformat()}")
    frame = load_day_frame(repo, commits)
    daily_dir = repo / DAILY_DIR
    daily_dir.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(daily_dir / f"{day.isoformat()}.parquet", compression="zstd")
    summary = summarize(day, frame)
    update_index(daily_dir / INDEX_FILENAME, summary)
    return summary


def write_station_information(stations: list[StationInfo], path: Path) -> None:
    """Writes the static station information as CSV, sorted by station_id."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(STATION_COLUMNS)
        for station in sorted(stations, key=lambda item: item.station_id):
            writer.writerow(
                (
                    station.station_id,
                    station.station_code,
                    station.name,
                    station.lat,
                    station.lon,
                    station.capacity,
                )
            )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout
```

- [ ] **Step 5: Ajouter la sous-commande `compact` à `src/velib/collect/cli.py`**

Remplacer tout le fichier par :

```python
"""Command line entry point: velib-collect snapshot | compact."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from velib import gbfs
from velib.collect.compact import (
    STATIONS_PATH,
    CompactionError,
    compact_day,
    write_station_information,
)
from velib.collect.snapshot import write_snapshot

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-collect command line.

    Args:
        argv: Arguments without the program name. Defaults to sys.argv[1:].

    Returns:
        The process exit code.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            return _snapshot(args.output_dir)
        return _compact(args.repo, args.day)
    except (gbfs.GbfsError, CompactionError) as error:
        logger.error("%s", error)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="velib-collect", description="Collect Vélib' station data for velib-data."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="Fetch station_status and write the raw files.")
    snapshot.add_argument(
        "--output-dir", type=Path, required=True, help="Directory that receives the raw files."
    )
    compact = commands.add_parser("compact", help="Compact the snapshots of one UTC day.")
    compact.add_argument(
        "--day",
        type=date.fromisoformat,
        default=None,
        help="UTC day as YYYY-MM-DD. Defaults to yesterday.",
    )
    compact.add_argument(
        "--repo", type=Path, default=Path(), help="Path to the velib-data checkout."
    )
    return parser


def _snapshot(output_dir: Path) -> int:
    with gbfs.make_client() as client:
        snapshot = gbfs.fetch_station_status(client)
    fetched_at = datetime.now(UTC)
    message = write_snapshot(snapshot, output_dir, fetched_at)
    logger.info("wrote %d stations to %s", len(snapshot.stations), output_dir)
    sys.stdout.write(message + "\n")
    return 0


def _compact(repo: Path, day: date | None) -> int:
    target = day or datetime.now(UTC).date() - timedelta(days=1)
    summary = compact_day(repo, target)
    with gbfs.make_client() as client:
        stations = gbfs.fetch_station_information(client)
    write_station_information(stations, repo / STATIONS_PATH)
    logger.info(
        "compacted %s: %d snapshots, %d rows, largest gap %.1f min",
        target.isoformat(),
        summary.snapshots,
        summary.rows,
        summary.max_gap_minutes,
    )
    return 0
```

- [ ] **Step 6: Vérifier que les tests passent**

Run: `uv run pytest -q`
Expected: PASS (29 tests).

- [ ] **Step 7: Passer les hooks et committer**

```bash
git add pyproject.toml uv.lock src/velib/collect tests/integration/test_compact.py
uv run pre-commit run --all-files
git commit -q -m "feat: add daily compaction command" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 6: Publier `collector-v2` et brancher le regroupement

**Files (dépôt `velib-data`):**
- Create: `.github/workflows/compact.yml`
- Modify: `.github/workflows/collect.yml` (ligne `ref: collector-v1` vers `ref: collector-v2`)

**Interfaces:**
- Consumes: `velib-collect compact --day AAAA-MM-JJ --repo .`
- Produces: chaque nuit, `daily/<veille>.parquet`, `daily/index.csv`, `stations/station_information.csv`

- [ ] **Step 1: Fusionner, vérifier, poser le tag et pousser**

```bash
git switch main
git merge --ff-only feat/compaction
make lint
make test
git log --format='%an <%ae> | %cn <%ce>' origin/main..main | sort -u
git log -p origin/main..main | grep -ciEf .git/info/private-identifiers
git push origin main
git tag -a collector-v2 -m "collector: snapshots and daily compaction"
git push origin collector-v2
```

Expected: hooks et tests au vert, une seule identité `tibzsecondaire`, compteur à `0`, tag poussé.

- [ ] **Step 2: Écrire `.github/workflows/compact.yml` dans la copie de travail de `velib-data`**

```yaml
name: compact

on:
  schedule:
    - cron: "15 0 * * *"
  workflow_dispatch:
    inputs:
      day:
        description: "UTC day to compact, as YYYY-MM-DD (default: yesterday)"
        required: false
        default: ""

concurrency:
  group: velib-data
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  compact:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Check out velib-data
        uses: actions/checkout@v7
        with:
          fetch-depth: 1

      - name: Pick the day and fetch its history
        id: day
        env:
          INPUT_DAY: ${{ inputs.day }}
        run: |
          day="${INPUT_DAY:-$(date -u -d yesterday +%F)}"
          echo "day=${day}" >> "$GITHUB_OUTPUT"
          git fetch --quiet --shallow-since="$(date -u -d "${day} -1 day" +%F)" origin main

      - name: Check out the velib code
        uses: actions/checkout@v7
        with:
          repository: tibzsecondaire/velib
          ref: collector-v2
          path: code
          persist-credentials: false

      - name: Install uv
        uses: astral-sh/setup-uv@v10.2.0
        with:
          enable-cache: true
          cache-dependency-glob: code/uv.lock

      - name: Compact the day
        env:
          DAY: ${{ steps.day.outputs.day }}
        run: uv run --project code --no-dev --frozen velib-collect compact --day "$DAY" --repo .

      - name: Commit and push
        env:
          DAY: ${{ steps.day.outputs.day }}
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add daily stations
          if git diff --cached --quiet; then
            echo "nothing changed for ${DAY}"
            exit 0
          fi
          git commit --quiet -m "compact ${DAY}"
          for attempt in 1 2 3; do
            if git push --quiet; then
              exit 0
            fi
            echo "push rejected (attempt ${attempt}), rebasing"
            git pull --rebase --quiet
          done
          exit 1
```

- [ ] **Step 3: Passer la collecte sur `collector-v2`, committer et pousser**

```bash
WORK="${WORK:-${TMPDIR:-/tmp}/velib-work}"
cd "$WORK/velib-data"
git pull -q --rebase
sed -i '' 's/ref: collector-v1/ref: collector-v2/' .github/workflows/collect.yml
grep -n 'ref: collector' .github/workflows/*.yml
git add .github/workflows
git commit -q -m "build: compact each day into parquet" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push -q
```

Expected: les 2 fichiers affichent `ref: collector-v2`.

- [ ] **Step 4: Essayer le regroupement sur la journée en cours**

```bash
export GH_CONFIG_DIR="$HOME/.config/gh-tibzsecondaire"
test "$(gh api user --jq .login)" = "tibzsecondaire"
gh workflow run compact.yml --repo tibzsecondaire/velib-data --ref main -f day="$(date -u +%F)"
gh run list --repo tibzsecondaire/velib-data --workflow compact.yml --event workflow_dispatch --limit 1
run_id="$(gh run list --repo tibzsecondaire/velib-data --workflow compact.yml --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$run_id" --repo tibzsecondaire/velib-data --exit-status
curl -s https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/daily/index.csv
```

Expected: run réussi ; `index.csv` contient la ligne du jour avec plusieurs relevés. Ce fichier partiel sera
remplacé par le regroupement de la nuit suivante.

## Phase C : chargement et heatmap

### Task 7: Chargement des données (`velib.dataset`)

**Files:**
- Create: `src/velib/dataset.py`
- Create: `tests/unit/test_dataset.py`

**Interfaces:**
- Consumes: `velib.DATA_DIR`, `velib.gbfs.make_client`
- Produces:
  - `DATA_SOURCE = "https://raw.githubusercontent.com/tibzsecondaire/velib-data/main"`, `CACHE_DIR = DATA_DIR / "cache"`
  - `load_day(day: date, *, source: str | Path = DATA_SOURCE, cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None) -> pl.DataFrame` (lève `FileNotFoundError` si le jour n'existe pas)
  - `load_stations(*, source: str | Path = DATA_SOURCE, cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None, refresh: bool = False) -> pl.DataFrame`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/unit/test_dataset.py` :

```python
"""Tests for loading velib-data files."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest

from velib import dataset

DAY = date(2026, 10, 5)
REMOTE = "https://example.org/velib-data"


def _frame() -> pl.DataFrame:
    return pl.DataFrame({"station_id": [1, 2], "mechanical": [3, 4]})


def _parquet_bytes() -> bytes:
    buffer = io.BytesIO()
    _frame().write_parquet(buffer)
    return buffer.getvalue()


def test_load_day_from_a_local_source_fills_the_cache(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "daily").mkdir(parents=True)
    _frame().write_parquet(source / "daily" / "2026-10-05.parquet")
    cache = tmp_path / "cache"
    assert dataset.load_day(DAY, source=source, cache_dir=cache).equals(_frame())
    (source / "daily" / "2026-10-05.parquet").unlink()
    assert dataset.load_day(DAY, source=source, cache_dir=cache).equals(_frame())


def test_load_day_missing_from_a_local_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        dataset.load_day(DAY, source=tmp_path, cache_dir=tmp_path / "cache")


def test_load_day_downloads_from_a_url(tmp_path: Path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=_parquet_bytes())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame = dataset.load_day(DAY, source=REMOTE, cache_dir=tmp_path, client=client)
    assert frame.equals(_frame())
    assert requested == [f"{REMOTE}/daily/2026-10-05.parquet"]


def test_load_day_turns_a_404_into_file_not_found(tmp_path: Path) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client,
        pytest.raises(FileNotFoundError),
    ):
        dataset.load_day(DAY, source=REMOTE, cache_dir=tmp_path, client=client)


def test_load_stations_keeps_station_codes_as_text(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "stations").mkdir(parents=True)
    (source / "stations" / "station_information.csv").write_text(
        "station_id,station_code,name,lat,lon,capacity\n1,00001,A,48.8,2.3,20\n", encoding="utf-8"
    )
    stations = dataset.load_stations(source=source, cache_dir=tmp_path / "cache")
    assert stations.get_column("station_code").to_list() == ["00001"]
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `uv run pytest tests/unit/test_dataset.py -q`
Expected: FAIL avec `ImportError: cannot import name 'dataset' from 'velib'`.

- [ ] **Step 3: Écrire `src/velib/dataset.py`**

```python
"""Load velib-data files (daily Parquet, station information) through a local cache."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import polars as pl

from velib import DATA_DIR, gbfs

DATA_SOURCE = "https://raw.githubusercontent.com/tibzsecondaire/velib-data/main"
CACHE_DIR = DATA_DIR / "cache"
STATIONS_FILE = "stations/station_information.csv"


def load_day(
    day: date,
    *,
    source: str | Path = DATA_SOURCE,
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
) -> pl.DataFrame:
    """Loads one compacted UTC day, downloading it once into the cache.

    Args:
        day: UTC day to load.
        source: Base URL of velib-data, or a local directory with the same layout.
        cache_dir: Local cache directory.
        client: HTTP client to reuse. A new one is created when None.

    Returns:
        One row per station and per snapshot.

    Raises:
        FileNotFoundError: The day has not been compacted.
    """
    relative_path = f"daily/{day.isoformat()}.parquet"
    cached = cache_dir / relative_path
    if not cached.exists():
        _store(cached, _fetch(relative_path, source, client))
    return pl.read_parquet(cached)


def load_stations(
    *,
    source: str | Path = DATA_SOURCE,
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> pl.DataFrame:
    """Loads the station information: code, name, coordinates and capacity.

    Args:
        source: Base URL of velib-data, or a local directory with the same layout.
        cache_dir: Local cache directory.
        client: HTTP client to reuse. A new one is created when None.
        refresh: Download again even when the file is cached.

    Returns:
        One row per station.
    """
    cached = cache_dir / STATIONS_FILE
    if refresh or not cached.exists():
        _store(cached, _fetch(STATIONS_FILE, source, client))
    return pl.read_csv(cached, schema_overrides={"station_code": pl.String})


def _fetch(relative_path: str, source: str | Path, client: httpx.Client | None) -> bytes:
    if isinstance(source, Path):
        path = source / relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path.read_bytes()
    url = f"{source.rstrip('/')}/{relative_path}"
    if client is None:
        with gbfs.make_client() as own_client:
            return _download(own_client, url)
    return _download(client, url)


def _download(client: httpx.Client, url: str) -> bytes:
    response = client.get(url)
    if response.status_code == httpx.codes.NOT_FOUND:
        raise FileNotFoundError(url)
    response.raise_for_status()
    return response.content


def _store(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `uv run pytest tests/unit/test_dataset.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Committer**

```bash
git switch -c feat/heatmap
git add src/velib/dataset.py tests/unit/test_dataset.py
uv run pre-commit run --all-files
git commit -q -m "feat: load velib-data files with a local cache" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 8: Heatmap d'une journée

**Files:**
- Modify: `pyproject.toml` (groupe `analysis` avec plotly)
- Create: `src/velib/heatmap.py`
- Create: `tests/unit/test_heatmap.py`
- Create: `scripts/heatmap_day.py`
- Delete: `scripts/.gitkeep`

**Interfaces:**
- Consumes: `velib.dataset.load_day`, `load_stations`, `velib.DATA_DIR`
- Produces: `velib.heatmap.fill_rate_by_slot(frame: pl.DataFrame, day: date, *, slot: str = "15m",
  time_zone: str = "Europe/Paris") -> pl.DataFrame` (colonnes `station_id`, `slot`, `fill_rate`) ;
  `uv run --group analysis python scripts/heatmap_day.py AAAA-MM-JJ` écrit `data/figures/heatmap_AAAA-MM-JJ.html`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/unit/test_heatmap.py` :

```python
"""Tests for the fill-rate heatmap data."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl

from velib.heatmap import fill_rate_by_slot

SCHEMA = {
    "fetched_at": pl.Datetime("us", "UTC"),
    "station_id": pl.Int64(),
    "mechanical": pl.Int16(),
    "ebike": pl.Int16(),
    "docks": pl.Int16(),
}


def test_fill_rate_is_averaged_per_paris_time_slot() -> None:
    frame = pl.DataFrame(
        [
            (datetime(2026, 10, 4, 21, 59, tzinfo=UTC), 1, 5, 0, 5),
            (datetime(2026, 10, 4, 22, 2, tzinfo=UTC), 1, 1, 1, 2),
            (datetime(2026, 10, 4, 22, 7, tzinfo=UTC), 1, 3, 0, 1),
            (datetime(2026, 10, 4, 22, 16, tzinfo=UTC), 1, 0, 0, 0),
        ],
        schema=SCHEMA,
        orient="row",
    )
    rates = fill_rate_by_slot(frame, date(2026, 10, 5))
    assert rates.get_column("slot").dt.strftime("%H:%M").to_list() == ["00:00", "00:15"]
    assert rates.get_column("fill_rate").to_list() == [0.625, None]
```

Les 4 lignes tombent à 23 h 59 le 4 octobre (exclue), puis 0 h 02, 0 h 07 et 0 h 16 le 5 octobre, heure de Paris
(UTC+2). Les 2 premières du 5 donnent 2/4 et 3/4, soit une moyenne de 0,625. La dernière n'a ni vélo ni place : null.

- [ ] **Step 2: Vérifier que le test échoue**

Run: `uv run pytest tests/unit/test_heatmap.py -q`
Expected: FAIL avec `ModuleNotFoundError: No module named 'velib.heatmap'`.

- [ ] **Step 3: Écrire `src/velib/heatmap.py`**

```python
"""Fill-rate data for the daily heatmap: one value per station and per time slot."""

from __future__ import annotations

from datetime import date

import polars as pl

PARIS_TIME_ZONE = "Europe/Paris"


def fill_rate_by_slot(
    frame: pl.DataFrame, day: date, *, slot: str = "15m", time_zone: str = PARIS_TIME_ZONE
) -> pl.DataFrame:
    """Averages the fill rate of each station per time slot of one local day.

    The fill rate is bikes / (bikes + free docks). It is null when a station has
    neither bikes nor free docks.

    Args:
        frame: Snapshot rows with fetched_at (UTC), station_id, mechanical, ebike and docks.
        day: Local day to keep.
        slot: Slot length, as a polars duration string.
        time_zone: Time zone that defines the local day and the slots.

    Returns:
        Columns station_id, slot (local time) and fill_rate, sorted by station and slot.
    """
    bikes = pl.col("mechanical").cast(pl.Int32) + pl.col("ebike").cast(pl.Int32)
    total = bikes + pl.col("docks").cast(pl.Int32)
    local_time = pl.col("fetched_at").dt.convert_time_zone(time_zone)
    return (
        frame.filter(local_time.dt.date() == day)
        .with_columns(
            local_time.dt.truncate(slot).alias("slot"),
            pl.when(total > 0).then(bikes / total).alias("fill_rate"),
        )
        .group_by("station_id", "slot")
        .agg(pl.col("fill_rate").mean())
        .sort("station_id", "slot")
    )
```

- [ ] **Step 4: Vérifier que le test passe**

Run: `uv run pytest tests/unit/test_heatmap.py -q`
Expected: PASS.

- [ ] **Step 5: Ajouter plotly et écrire le script**

```bash
uv add --group analysis plotly
git rm -q scripts/.gitkeep
```

Créer `scripts/heatmap_day.py` :

```python
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
```

- [ ] **Step 6: Tracer la journée en cours pour vérifier le script**

```bash
uv run --group analysis python scripts/heatmap_day.py "$(date -u +%F)"
ls -lh data/figures/
```

Expected: un fichier `heatmap_<date>.html` ; ouvert dans un navigateur, il montre les tranches déjà regroupées.

- [ ] **Step 7: Committer, fusionner et pousser**

```bash
git add pyproject.toml uv.lock src/velib/heatmap.py tests/unit/test_heatmap.py scripts
uv run pre-commit run --all-files
git commit -q -m "feat: plot the fill-rate heatmap of a day" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git switch main
git merge --ff-only feat/heatmap
make lint
make test
git log --format='%an <%ae> | %cn <%ce>' origin/main..main | sort -u
git log -p origin/main..main | grep -ciEf .git/info/private-identifiers
git push origin main
```

Expected: hooks et tests au vert, une seule identité, compteur à `0`.

### Task 9: Critère de réussite et suivi

**Files:** aucun

- [ ] **Step 1: Après la première journée complète, vérifier le regroupement nocturne**

Le 7 octobre 2026 après 0 h 15 UTC :

```bash
curl -s https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/daily/index.csv
```

Expected: une ligne `2026-10-06` avec environ 288 relevés (moins si GitHub en a sauté) et un plus grand écart affiché.

- [ ] **Step 2: Tracer la heatmap du 6 octobre**

```bash
uv run --group analysis python scripts/heatmap_day.py 2026-10-06
```

Expected: `data/figures/heatmap_2026-10-06.html` couvre toute la journée, de 00:00 à 23:45 heure de Paris.
C'est le critère de réussite du design.

- [ ] **Step 3: Après une semaine, mesurer la croissance de `velib-data`**

```bash
export GH_CONFIG_DIR="$HOME/.config/gh-tibzsecondaire"
gh api repos/tibzsecondaire/velib-data --jq '.size'
```

Expected: la taille en Ko divisée par le nombre de jours de collecte reste sous 3 000 Ko par jour. Au-delà, revoir la
fréquence des relevés ou le stockage de l'historique ancien, comme prévu dans le design.
