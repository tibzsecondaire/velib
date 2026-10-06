# Plan d'implémentation de la prévision de disponibilité

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prévoir les vélos disponibles à 15, 30, 60, 120 et 180 minutes sur les deux semaines Kaggle, et mesurer le
modèle face à 3 références.

**Architecture:** Le package `velib.forecast` met les relevés sur une grille de 5 minutes, construit un exemple par
station et instant, entraîne un `HistGradientBoostingRegressor` par horizon et compare ses prévisions aux références
sur les 4 derniers jours. La commande `velib-forecast evaluate` écrit `metrics.json` et `report.md`.

**Tech Stack:** Python 3.12, polars 1.44, scikit-learn (groupe `ml`), numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-prevision-design.md`

## Global Constraints

- horizons 15, 30, 60, 120 et 180 minutes ; test à partir du `2025-12-13T00:00:00Z`
- seules les informations connues à t servent ; profils calculés sur l'apprentissage seulement
- couples évalués seulement si la station est en service à t et à t + h
- modèle : `HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, max_leaf_nodes=63, l2_regularization=1.0,
  early_stopping=False, random_state=0)`, cible = variation de vélos, prévision bornée entre 0 et la capacité
- scikit-learn dans le groupe `ml` ; mypy ignore ses types manquants
- aucun appel réseau dans les tests
- mêmes règles que les plans précédents (identité, contrôle avant push, Conventional Commits, extraction des fichiers
  avec `<!-- file: chemin -->`)

---

### Task 1: Variables et exemples

**Files:**
- Create: `src/velib/forecast/__init__.py`, `src/velib/forecast/features.py`, `tests/unit/test_forecast_features.py`

**Interfaces:**
- Produces: `STEP_MINUTES = 5`, `LAGS`, `FEATURES`, `WEATHER_COLUMNS`, `build_grid(frame) -> pl.DataFrame`,
  `calendar(time, prefix="") -> list[pl.Expr]`, `station_profiles(train) -> tuple[pl.DataFrame, pl.DataFrame]`,
  `build_examples(grid, horizon_minutes, profiles, stations=None, weather=None, origins=None) -> pl.DataFrame`

- [ ] **Step 1: Écrire les tests qui échouent**

<!-- file: tests/unit/test_forecast_features.py -->
```python
"""Tests for the forecasting features."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from velib.forecast.features import FEATURES, build_examples, build_grid, station_profiles

START = datetime(2025, 12, 6, 9, 0, tzinfo=UTC)


def _frame(slots: int, bikes_of: dict[int, int] | None = None) -> pl.DataFrame:
    rows = []
    for station_id in (1, 2):
        for slot in range(slots):
            bikes = (bikes_of or {}).get(slot, slot)
            rows.append(
                {
                    "fetched_at": START + timedelta(minutes=5 * slot, seconds=30),
                    "station_id": station_id,
                    "mechanical": bikes,
                    "ebike": 0,
                    "docks": 40 - bikes,
                    "is_installed": True,
                    "is_renting": station_id == 1 or slot != 2,
                    "is_returning": True,
                }
            )
    return pl.DataFrame(rows, schema_overrides={"fetched_at": pl.Datetime("us", "UTC")})


def test_build_grid_puts_snapshots_on_5_minute_slots() -> None:
    grid = build_grid(_frame(4))
    assert grid.height == 8
    assert grid.get_column("time").to_list()[0] == START
    assert grid.get_column("total").unique().to_list() == [40]
    first = grid.filter(pl.col("station_id") == 1).get_column("t").to_list()
    assert [t - first[0] for t in first] == [0, 1, 2, 3]
    assert grid.filter(pl.col("station_id") == 2).get_column("is_open").to_list() == [
        True,
        True,
        False,
        True,
    ]


def test_build_examples_aligns_targets_lags_and_calendar() -> None:
    grid = build_grid(_frame(30))
    profiles = station_profiles(grid)
    examples = build_examples(grid, 15, profiles).filter(pl.col("station_id") == 1)
    row = examples.filter(pl.col("bikes") == 20).row(0, named=True)
    assert row["target"] == 23
    assert row["bikes_lag1"] == 19
    assert row["bikes_lag12"] == 8
    assert row["delta3"] == 3
    assert row["is_weekend"] is True
    assert row["target_hour"] == 11
    assert set(FEATURES) <= set(examples.columns)


def test_build_examples_skips_closed_stations_at_t_and_t_plus_h() -> None:
    grid = build_grid(_frame(10))
    examples = build_examples(grid, 5, station_profiles(grid)).filter(pl.col("station_id") == 2)
    assert 1 not in examples.get_column("bikes").to_list()
    assert 2 not in examples.get_column("bikes").to_list()


def test_profiles_use_only_the_rows_given() -> None:
    grid = build_grid(
        _frame(24, bikes_of=dict.fromkeys(range(12), 10) | dict.fromkeys(range(12, 24), 30))
    )
    train = grid.filter(pl.col("time") < START + timedelta(hours=1))
    by_day_type, all_days = station_profiles(train)
    assert by_day_type.filter(pl.col("station_id") == 1).get_column("profile").to_list() == [10.0]
    assert all_days.filter(pl.col("station_id") == 1).get_column("hour").to_list() == [10]


def test_build_examples_can_keep_only_some_origins() -> None:
    grid = build_grid(_frame(30))
    examples = build_examples(grid, 15, station_profiles(grid), origins=pl.col("t") % 3 == 0)
    assert (examples.get_column("t") % 3).unique().to_list() == [0]
```

Run: `uv run pytest tests/unit/test_forecast_features.py -q`
Expected: FAIL avec `ModuleNotFoundError: No module named 'velib.forecast'`.

- [ ] **Step 2: Écrire le module**

<!-- file: src/velib/forecast/__init__.py -->
```python
"""Forecasting of the availability of Vélib' stations."""
```

<!-- file: src/velib/forecast/features.py -->
```python
"""Features for availability forecasting: a 5-minute grid, station profiles and examples."""

from __future__ import annotations

import math

import polars as pl

STEP_MINUTES = 5
PARIS_TIME_ZONE = "Europe/Paris"
LAGS = (1, 3, 6, 12)
WEATHER_COLUMNS = ("temp_c", "precip_mm", "wind_mps")
FEATURES = (
    "bikes",
    "ebike",
    "docks",
    "total",
    "fill",
    *(f"bikes_lag{lag}" for lag in LAGS),
    *(f"delta{lag}" for lag in LAGS),
    "hour_sin",
    "hour_cos",
    "target_hour_sin",
    "target_hour_cos",
    "weekday",
    "is_weekend",
    "profile_now",
    "profile_target",
    "profile_delta",
    "profile_all_target",
    "lat",
    "lon",
    *WEATHER_COLUMNS,
)


def build_grid(frame: pl.DataFrame) -> pl.DataFrame:
    """Puts snapshots on a regular 5-minute grid, one row per station and slot.

    Args:
        frame: Daily-schema rows: fetched_at, station_id, mechanical, ebike, docks and statuses.

    Returns:
        Columns station_id, t (slot number since 1970), time (UTC start of the slot), bikes,
        ebike, docks, total (bikes + docks) and is_open, sorted by station and slot. When a slot
        has several snapshots, the last one wins.
    """
    bikes = pl.col("mechanical").cast(pl.Int32) + pl.col("ebike").cast(pl.Int32)
    return (
        frame.sort("fetched_at")
        .with_columns(pl.col("fetched_at").dt.truncate(f"{STEP_MINUTES}m").alias("time"))
        .unique(subset=["station_id", "time"], keep="last", maintain_order=True)
        .select(
            "station_id",
            (pl.col("time").dt.epoch("s") // (STEP_MINUTES * 60)).alias("t"),
            "time",
            bikes.alias("bikes"),
            pl.col("ebike").cast(pl.Int32),
            pl.col("docks").cast(pl.Int32),
            (bikes + pl.col("docks").cast(pl.Int32)).alias("total"),
            (pl.col("is_installed") & pl.col("is_renting")).alias("is_open"),
        )
        .sort("station_id", "t")
    )


def calendar(time: pl.Expr, prefix: str = "") -> list[pl.Expr]:
    """Hour of day, its sine and cosine, weekday and day type of a UTC time, in Paris time."""
    local = time.dt.convert_time_zone(PARIS_TIME_ZONE)
    angle = (local.dt.hour() + local.dt.minute() / 60) * (2 * math.pi / 24)
    return [
        local.dt.hour().alias(f"{prefix}hour"),
        angle.sin().alias(f"{prefix}hour_sin"),
        angle.cos().alias(f"{prefix}hour_cos"),
        local.dt.weekday().alias(f"{prefix}weekday"),
        (local.dt.weekday() >= 6).alias(f"{prefix}is_weekend"),
    ]


def station_profiles(train: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Mean bikes of each station by hour and day type, and by hour over all days.

    Args:
        train: Grid rows of the training period only.

    Returns:
        A table keyed by station_id, is_weekend and hour, with a profile column, and a table
        keyed by station_id and hour, with a profile_all column.
    """
    rows = train.filter(pl.col("is_open")).with_columns(calendar(pl.col("time")))
    by_day_type = rows.group_by("station_id", "is_weekend", "hour").agg(
        pl.col("bikes").mean().alias("profile")
    )
    all_days = rows.group_by("station_id", "hour").agg(pl.col("bikes").mean().alias("profile_all"))
    return by_day_type, all_days


def build_examples(
    grid: pl.DataFrame,
    horizon_minutes: int,
    profiles: tuple[pl.DataFrame, pl.DataFrame],
    stations: pl.DataFrame | None = None,
    weather: pl.DataFrame | None = None,
    origins: pl.Expr | None = None,
) -> pl.DataFrame:
    """Builds one example per station and slot: features at t and bikes at t + horizon.

    Only pairs where the station is open at t and at t + horizon are kept.

    Args:
        grid: Output of build_grid.
        horizon_minutes: Forecast horizon, a multiple of 5 minutes.
        profiles: Output of station_profiles, computed on the training period.
        stations: Station information with station_id, lat and lon.
        weather: Weather by slot, with time_bin and WEATHER_COLUMNS.
        origins: Filter on the grid rows used as forecast origins.

    Returns:
        The examples, with FEATURES, target (bikes at t + horizon), time and target_time.
    """
    steps = horizon_minutes // STEP_MINUTES
    by_day_type, all_days = profiles
    keep = pl.col("is_open") if origins is None else pl.col("is_open") & origins
    future = grid.filter(pl.col("is_open")).select(
        "station_id", (pl.col("t") - steps).alias("t"), pl.col("bikes").alias("target")
    )
    examples = grid.filter(keep).join(future, on=["station_id", "t"], how="inner")
    for lag in LAGS:
        past = grid.select(
            "station_id", (pl.col("t") + lag).alias("t"), pl.col("bikes").alias(f"bikes_lag{lag}")
        )
        examples = examples.join(past, on=["station_id", "t"], how="left")
    examples = (
        examples.with_columns(
            *calendar(pl.col("time")),
            (pl.col("time") + pl.duration(minutes=horizon_minutes)).alias("target_time"),
            pl.when(pl.col("total") > 0).then(pl.col("bikes") / pl.col("total")).alias("fill"),
            *[(pl.col("bikes") - pl.col(f"bikes_lag{lag}")).alias(f"delta{lag}") for lag in LAGS],
        )
        .with_columns(calendar(pl.col("target_time"), prefix="target_"))
        .join(
            by_day_type.rename({"profile": "profile_now"}),
            on=["station_id", "is_weekend", "hour"],
            how="left",
        )
        .join(
            by_day_type.rename(
                {
                    "is_weekend": "target_is_weekend",
                    "hour": "target_hour",
                    "profile": "profile_target",
                }
            ),
            on=["station_id", "target_is_weekend", "target_hour"],
            how="left",
        )
        .join(
            all_days.rename({"hour": "target_hour", "profile_all": "profile_all_target"}),
            on=["station_id", "target_hour"],
            how="left",
        )
        .with_columns((pl.col("profile_target") - pl.col("profile_now")).alias("profile_delta"))
    )
    if stations is None:
        examples = examples.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("lat"), pl.lit(None, dtype=pl.Float64).alias("lon")
        )
    else:
        examples = examples.join(
            stations.select("station_id", "lat", "lon"), on="station_id", how="left"
        )
    if weather is None:
        examples = examples.with_columns(
            *(pl.lit(None, dtype=pl.Float64).alias(name) for name in WEATHER_COLUMNS)
        )
    else:
        examples = examples.join(
            weather.select("time_bin", *WEATHER_COLUMNS).rename({"time_bin": "time"}),
            on="time",
            how="left",
        )
    return examples.sort("station_id", "t")
```

- [ ] **Step 3: Vérifier et committer**

Run: `uv run pytest tests/unit/test_forecast_features.py -q`
Expected: PASS (5 tests).

```bash
git add src/velib/forecast tests/unit/test_forecast_features.py
uv run pre-commit run --all-files
git commit -q -m "feat: build forecasting examples" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2: Références, modèle et mesures

**Files:**
- Create: `src/velib/forecast/baselines.py`, `src/velib/forecast/model.py`, `src/velib/forecast/metrics.py`,
  `tests/unit/test_forecast_model.py`
- Modify: `pyproject.toml` (groupe `ml` avec scikit-learn, règle mypy pour `sklearn`)

**Interfaces:**
- Consumes: `velib.forecast.features.FEATURES`
- Produces: `BASELINES: dict[str, Callable[[pl.DataFrame], pl.Series]]`, `train(examples, *, max_iter=300)`,
  `predict(model, examples) -> pl.Series`, `feature_matrix(examples) -> NDArray[float64]`,
  `evaluate(truth, prediction, total) -> dict[str, float]`

- [ ] **Step 1: Ajouter scikit-learn**

```bash
uv add --group ml scikit-learn
```

Dans `pyproject.toml`, après le bloc `[[tool.mypy.overrides]]` existant, ajouter :

```toml
[[tool.mypy.overrides]]
module = ["sklearn", "sklearn.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Écrire les tests qui échouent**

<!-- file: tests/unit/test_forecast_model.py -->
```python
"""Tests for the forecasting baselines, model and metrics."""

from __future__ import annotations

import polars as pl
import pytest

from velib.forecast.baselines import BASELINES
from velib.forecast.features import FEATURES
from velib.forecast.metrics import evaluate
from velib.forecast.model import predict, train


def _examples(rows: int) -> pl.DataFrame:
    hours = [index % 24 for index in range(rows)]
    profile_delta = [4.0 if hour < 12 else -4.0 for hour in hours]
    bikes = [10 + (index % 5) for index in range(rows)]
    data: dict[str, list[float]] = {name: [0.0] * rows for name in FEATURES}
    data["bikes"] = [float(value) for value in bikes]
    data["total"] = [30.0] * rows
    data["profile_delta"] = profile_delta
    data["profile_target"] = [b + d for b, d in zip(bikes, profile_delta, strict=True)]
    frame = pl.DataFrame(data)
    return frame.with_columns(
        (pl.col("bikes") + pl.col("profile_delta")).alias("target"),
        pl.col("bikes").cast(pl.Int32),
        pl.col("total").cast(pl.Int32),
    )


def test_baselines() -> None:
    examples = pl.DataFrame(
        {
            "bikes": [5, 1, 9],
            "total": [10, 10, 10],
            "profile_target": [7.0, None, 3.0],
            "profile_delta": [2.0, -3.0, 4.0],
        }
    )
    assert BASELINES["persistence"](examples).to_list() == [5.0, 1.0, 9.0]
    assert BASELINES["profile"](examples).to_list() == [7.0, 1.0, 3.0]
    assert BASELINES["adjusted persistence"](examples).to_list() == [7.0, 0.0, 10.0]


def test_evaluate_reports_errors_and_empty_station_detection() -> None:
    truth = pl.Series([0, 2, 4, 0])
    prediction = pl.Series([0.2, 3.0, 4.0, 1.0])
    scores = evaluate(truth, prediction, pl.Series([10, 10, 10, 10]))
    assert scores["mae"] == pytest.approx(0.55)
    assert scores["rmse"] == pytest.approx((0.04 + 1 + 0 + 1) ** 0.5 / 2)
    assert scores["fill_mae"] == pytest.approx(0.055)
    assert scores["empty_precision"] == 1.0
    assert scores["empty_recall"] == 0.5
    assert scores["count"] == 4


@pytest.mark.parametrize("loss", ["absolute_error", "squared_error"])
def test_model_learns_the_daily_pattern_better_than_persistence(loss: str) -> None:
    examples = _examples(2000)
    model = train(examples, max_iter=60, loss=loss)
    prediction = predict(model, examples)
    model_mae = evaluate(examples.get_column("target"), prediction, examples.get_column("total"))[
        "mae"
    ]
    persistence = BASELINES["persistence"](examples)
    persistence_mae = evaluate(
        examples.get_column("target"), persistence, examples.get_column("total")
    )["mae"]
    assert model_mae < 0.25 * persistence_mae
    assert (prediction >= 0).all()
    assert (prediction <= 30).all()
```

Run: `uv run --group ml pytest tests/unit/test_forecast_model.py -q`
Expected: FAIL avec `ModuleNotFoundError: No module named 'velib.forecast.baselines'`.

- [ ] **Step 3: Écrire les modules**

<!-- file: src/velib/forecast/baselines.py -->
```python
"""Reference forecasts to compare the model with."""

from __future__ import annotations

from collections.abc import Callable

import polars as pl


def persistence(examples: pl.DataFrame) -> pl.Series:
    """Bikes at t + horizon = bikes at t."""
    return examples.get_column("bikes").cast(pl.Float64).alias("persistence")


def profile(examples: pl.DataFrame) -> pl.Series:
    """Usual bikes of the station at the hour and day type of t + horizon."""
    return examples.select(
        pl.coalesce(pl.col("profile_target"), pl.col("bikes").cast(pl.Float64)).alias("profile")
    ).to_series()


def adjusted_persistence(examples: pl.DataFrame) -> pl.Series:
    """Bikes at t plus the usual change between t and t + horizon, within the capacity."""
    return examples.select(
        (pl.col("bikes") + pl.col("profile_delta").fill_null(0.0))
        .clip(0, pl.col("total"))
        .cast(pl.Float64)
        .alias("adjusted persistence")
    ).to_series()


BASELINES: dict[str, Callable[[pl.DataFrame], pl.Series]] = {
    "persistence": persistence,
    "profile": profile,
    "adjusted persistence": adjusted_persistence,
}
```

<!-- file: src/velib/forecast/model.py -->
```python
"""Gradient-boosted model of the change of bikes between t and t + horizon."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor

from velib.forecast.features import FEATURES


def feature_matrix(examples: pl.DataFrame) -> npt.NDArray[np.float64]:
    """FEATURES as a float matrix, with NaN for missing values."""
    return examples.select(pl.col(name).cast(pl.Float64) for name in FEATURES).to_numpy()


def train(
    examples: pl.DataFrame, *, max_iter: int = 300, loss: str = "absolute_error"
) -> HistGradientBoostingRegressor:
    """Fits a model of the change of bikes between t and t + horizon.

    Args:
        examples: Training examples with FEATURES, bikes and target.
        max_iter: Number of boosting iterations.
        loss: "absolute_error" forecasts the median change and minimises the MAE;
            "squared_error" forecasts the mean change and minimises the RMSE.

    Returns:
        The fitted model.
    """
    model = HistGradientBoostingRegressor(
        loss=loss,
        max_iter=max_iter,
        learning_rate=0.08,
        max_leaf_nodes=63,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=0,
    )
    change = (examples.get_column("target") - examples.get_column("bikes")).cast(pl.Float64)
    model.fit(feature_matrix(examples), change.to_numpy())
    return model


def predict(model: HistGradientBoostingRegressor, examples: pl.DataFrame) -> pl.Series:
    """Forecasts bikes at t + horizon, within 0 and the capacity of the station."""
    change = pl.Series("change", model.predict(feature_matrix(examples)), dtype=pl.Float64)
    return (
        examples.with_columns(change)
        .select((pl.col("bikes") + pl.col("change")).clip(0, pl.col("total")).alias("model"))
        .to_series()
    )
```

<!-- file: src/velib/forecast/metrics.py -->
```python
"""Accuracy measures of availability forecasts."""

from __future__ import annotations

import polars as pl


def evaluate(truth: pl.Series, prediction: pl.Series, total: pl.Series) -> dict[str, float]:
    """Measures a forecast against the truth.

    Args:
        truth: Bikes at t + horizon.
        prediction: Forecast bikes at t + horizon.
        total: Capacity of the station at t (bikes + free docks).

    Returns:
        mae and rmse in bikes, fill_mae as a share of the capacity, precision, recall and F1 of
        the detection of empty stations (forecast under 0.5 bike), and count.
    """
    frame = pl.DataFrame(
        {
            "truth": truth.cast(pl.Float64),
            "prediction": prediction.cast(pl.Float64),
            "total": total.cast(pl.Float64),
        }
    )
    error = pl.col("prediction") - pl.col("truth")
    stats = frame.select(
        error.abs().mean().alias("mae"),
        error.pow(2).mean().sqrt().alias("rmse"),
        (error.abs() / pl.col("total")).filter(pl.col("total") > 0).mean().alias("fill_mae"),
        ((pl.col("truth") == 0) & (pl.col("prediction") < 0.5)).sum().alias("tp"),
        ((pl.col("truth") != 0) & (pl.col("prediction") < 0.5)).sum().alias("fp"),
        ((pl.col("truth") == 0) & (pl.col("prediction") >= 0.5)).sum().alias("fn"),
    ).row(0, named=True)
    tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "mae": float(stats["mae"]),
        "rmse": float(stats["rmse"]),
        "fill_mae": float(stats["fill_mae"]),
        "empty_precision": precision,
        "empty_recall": recall,
        "empty_f1": f1,
        "count": float(frame.height),
    }
```

- [ ] **Step 4: Vérifier et committer**

Run: `uv run --group ml pytest tests/unit/test_forecast_model.py -q`
Expected: PASS (3 tests).

```bash
git add pyproject.toml uv.lock src/velib/forecast tests/unit/test_forecast_model.py
uv run --group ml pre-commit run --all-files
git commit -q -m "feat: add forecasting baselines, model and metrics" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 3: Commande `velib-forecast evaluate` et rapport

**Files:**
- Create: `src/velib/forecast/cli.py`, `tests/integration/test_forecast_cli.py`
- Modify: `pyproject.toml` (point d'entrée `velib-forecast = "velib.forecast.cli:main"`)

**Interfaces:**
- Consumes: tout `velib.forecast`, `velib.archives.ARCHIVES_DIR`
- Produces: `velib-forecast evaluate [--source NOM|DOSSIER] [--test-start ISO] [--horizons ...] [--out DOSSIER]
  [--max-iter N] [--importance-horizon N] [--sample N]`, qui écrit `metrics.json` et `report.md`

- [ ] **Step 1: Écrire le test qui échoue**

<!-- file: tests/integration/test_forecast_cli.py -->
```python
"""End-to-end test of velib-forecast evaluate on a tiny synthetic archive."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from velib.collect.compact import STATION_COLUMNS
from velib.forecast import cli

pytestmark = pytest.mark.integration

START = datetime(2025, 12, 1, 0, 0, tzinfo=UTC)


def _write_archive(root: Path) -> None:
    rows = []
    for slot in range(2 * 288):
        time = START + timedelta(minutes=5 * slot)
        for station_id in (1, 2, 3):
            bikes = round(10 + 8 * math.sin(2 * math.pi * slot / 288 + station_id))
            rows.append(
                {
                    "fetched_at": time + timedelta(seconds=20),
                    "feed_updated_at": None,
                    "station_id": station_id,
                    "mechanical": bikes,
                    "ebike": 0,
                    "docks": 25 - bikes,
                    "is_installed": True,
                    "is_renting": True,
                    "is_returning": True,
                    "last_reported": None,
                }
            )
    frame = pl.DataFrame(
        rows,
        schema_overrides={
            "fetched_at": pl.Datetime("us", "UTC"),
            "feed_updated_at": pl.Datetime("us", "UTC"),
            "last_reported": pl.Datetime("us", "UTC"),
            "mechanical": pl.Int16,
            "ebike": pl.Int16,
            "docks": pl.Int16,
        },
    )
    daily = root / "daily"
    daily.mkdir(parents=True)
    days = frame.with_columns(pl.col("fetched_at").dt.date().alias("day"))
    for key, part in days.partition_by("day", as_dict=True).items():
        part.drop("day").write_parquet(daily / f"{key[0].isoformat()}.parquet")
    stations = pl.DataFrame(
        {"station_id": [1, 2, 3], "station_code": ["", "", ""], "name": ["A", "B", "C"]}
    ).with_columns(
        pl.lit(48.85).alias("lat"), pl.lit(2.35).alias("lon"), pl.lit(25).alias("capacity")
    )
    (root / "stations").mkdir()
    stations.select(STATION_COLUMNS).write_csv(root / "stations" / "station_information.csv")
    weather = pl.DataFrame(
        {"time_bin": [START + timedelta(minutes=5 * slot) for slot in range(2 * 288)]}
    ).with_columns(
        pl.lit(8.0).alias("temp_c"), pl.lit(0.0).alias("precip_mm"), pl.lit(3.0).alias("wind_mps")
    )
    weather.with_columns(pl.col("time_bin").cast(pl.Datetime("us", "UTC"))).write_parquet(
        root / "weather.parquet"
    )


def test_evaluate_writes_metrics_and_report(tmp_path: Path) -> None:
    _write_archive(tmp_path / "archive")
    out = tmp_path / "out"
    exit_code = cli.main(
        [
            "evaluate",
            "--source",
            str(tmp_path / "archive"),
            "--test-start",
            "2025-12-02T00:00:00Z",
            "--horizons",
            "15",
            "60",
            "--max-iter",
            "20",
            "--importance-horizon",
            "60",
            "--sample",
            "200",
            "--out",
            str(out),
        ]
    )
    assert exit_code == 0
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert sorted(metrics["horizons"]) == ["15", "60"]
    scores = metrics["horizons"]["60"]["scores"]
    assert set(scores) == {
        "persistence",
        "profile",
        "adjusted persistence",
        "model",
        "model (squared loss)",
    }
    assert metrics["horizons"]["60"]["importance"]
    assert "| 60 min |" in (out / "report.md").read_text(encoding="utf-8")
```

Run: `uv run --group ml pytest tests/integration/test_forecast_cli.py -q`
Expected: FAIL avec `ImportError: cannot import name 'cli' from 'velib.forecast'`.

- [ ] **Step 2: Écrire la commande**

<!-- file: src/velib/forecast/cli.py -->
```python
"""Command line entry point: velib-forecast evaluate."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
from sklearn.inspection import permutation_importance

from velib import DATA_DIR
from velib.archives import ARCHIVES_DIR
from velib.forecast.baselines import BASELINES
from velib.forecast.features import (
    FEATURES,
    PARIS_TIME_ZONE,
    build_examples,
    build_grid,
    station_profiles,
)
from velib.forecast.metrics import evaluate
from velib.forecast.model import feature_matrix, predict, train

logger = logging.getLogger(__name__)

DEFAULT_HORIZONS = (15, 30, 60, 120, 180)
ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-forecast command line.

    Args:
        argv: Arguments without the program name. Defaults to sys.argv[1:].

    Returns:
        The process exit code.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    parser = argparse.ArgumentParser(
        prog="velib-forecast", description="Forecast the availability of Vélib' stations."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate_parser = commands.add_parser("evaluate", help="Train and score models on a source.")
    evaluate_parser.add_argument(
        "--source", default="kaggle", help="Imported archive name or velib-data directory."
    )
    evaluate_parser.add_argument(
        "--test-start",
        type=datetime.fromisoformat,
        default=datetime.fromisoformat("2025-12-13T00:00:00Z"),
        help="Start of the test period, ISO 8601 with time zone.",
    )
    evaluate_parser.add_argument(
        "--horizons", type=int, nargs="+", default=list(DEFAULT_HORIZONS), help="Minutes."
    )
    evaluate_parser.add_argument("--out", type=Path, default=None, help="Report directory.")
    evaluate_parser.add_argument("--max-iter", type=int, default=300)
    evaluate_parser.add_argument("--importance-horizon", type=int, default=60)
    evaluate_parser.add_argument("--sample", type=int, default=200_000)
    args = parser.parse_args(argv)

    source = _resolve_source(args.source)
    out_dir: Path = args.out or DATA_DIR / "forecast" / source.name
    report = run_evaluation(
        source,
        test_start=args.test_start,
        horizons=args.horizons,
        max_iter=args.max_iter,
        importance_horizon=args.importance_horizon,
        sample=args.sample,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown = render_report(report)
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")
    sys.stdout.write(markdown)
    logger.info("wrote %s", out_dir)
    return 0


def run_evaluation(
    source: Path,
    *,
    test_start: datetime,
    horizons: Sequence[int],
    max_iter: int,
    importance_horizon: int,
    sample: int,
) -> dict[str, Any]:
    """Trains one model per horizon and scores it with the baselines on the test period.

    Args:
        source: Directory with the velib-data layout.
        test_start: Start of the test period.
        horizons: Forecast horizons in minutes.
        max_iter: Boosting iterations of each model.
        importance_horizon: Horizon whose permutation importance is measured.
        sample: Number of test examples used for the permutation importance.

    Returns:
        A JSON-ready report.
    """
    frame = pl.concat(
        [pl.read_parquet(path) for path in sorted((source / "daily").glob("*.parquet"))]
    )
    stations_path = source / "stations" / "station_information.csv"
    stations = pl.read_csv(stations_path) if stations_path.exists() else None
    weather_path = source / "weather.parquet"
    weather = pl.read_parquet(weather_path) if weather_path.exists() else None
    grid = build_grid(frame)
    profiles = station_profiles(grid.filter(pl.col("time") < test_start))
    report: dict[str, Any] = {
        "source": source.name,
        "test_start": test_start.isoformat(),
        "first_snapshot": grid.select(pl.col("time").min().dt.strftime(ISO_UTC)).item(),
        "last_snapshot": grid.select(pl.col("time").max().dt.strftime(ISO_UTC)).item(),
        "stations": grid.get_column("station_id").n_unique(),
        "horizons": {},
    }
    for horizon in horizons:
        origins = (
            (pl.col("time") < test_start - pl.duration(minutes=horizon)) & (pl.col("t") % 3 == 0)
        ) | (pl.col("time") >= test_start)
        examples = build_examples(grid, horizon, profiles, stations, weather, origins)
        train_rows = examples.filter(pl.col("target_time") < test_start)
        test_rows = examples.filter(pl.col("time") >= test_start)
        logger.info(
            "horizon %d min: %d training and %d test examples",
            horizon,
            train_rows.height,
            test_rows.height,
        )
        model = train(train_rows, max_iter=max_iter)
        squared_model = train(train_rows, max_iter=max_iter, loss="squared_error")
        predictions = {name: forecast(test_rows) for name, forecast in BASELINES.items()}
        predictions["model"] = predict(model, test_rows)
        predictions["model (squared loss)"] = predict(squared_model, test_rows)
        truth = test_rows.get_column("target")
        total = test_rows.get_column("total")
        entry: dict[str, Any] = {
            "train_examples": train_rows.height,
            "test_examples": test_rows.height,
            "scores": {
                name: evaluate(truth, prediction, total) for name, prediction in predictions.items()
            },
            "per_day": _per_day(test_rows, predictions),
        }
        if horizon == importance_horizon:
            entry["importance"] = _importance(model, test_rows, sample)
        report["horizons"][str(horizon)] = entry
    return report


def render_report(report: dict[str, Any]) -> str:
    """Formats the report as Markdown, with one table per measure."""
    horizons = report["horizons"]
    methods = ["persistence", "profile", "adjusted persistence", "model", "model (squared loss)"]
    lines = [
        f"# Forecast evaluation on {report['source']}",
        "",
        f"Snapshots from {report['first_snapshot']} to {report['last_snapshot']}, "
        f"{report['stations']} stations. Test period from {report['test_start']}.",
        "",
        "## Mean absolute error, in bikes",
        "",
        "| Horizon | " + " | ".join(methods) + " | Model gain over persistence |",
        "|---|" + "---|" * (len(methods) + 1),
    ]
    for horizon, entry in horizons.items():
        scores = entry["scores"]
        gain = 1 - scores["model"]["mae"] / scores["persistence"]["mae"]
        cells = " | ".join(f"{scores[name]['mae']:.2f}" for name in methods)
        lines.append(f"| {horizon} min | {cells} | {gain:.0%} |")
    lines += ["", "## Root mean square error, in bikes", ""]
    lines += ["| Horizon | " + " | ".join(methods) + " |", "|---|" + "---|" * len(methods)]
    for horizon, entry in horizons.items():
        cells = " | ".join(f"{entry['scores'][name]['rmse']:.2f}" for name in methods)
        lines.append(f"| {horizon} min | {cells} |")
    lines += ["", "## Empty stations at the horizon: F1 of the detection", ""]
    lines += ["| Horizon | " + " | ".join(methods) + " |", "|---|" + "---|" * len(methods)]
    for horizon, entry in horizons.items():
        cells = " | ".join(f"{entry['scores'][name]['empty_f1']:.2f}" for name in methods)
        lines.append(f"| {horizon} min | {cells} |")
    lines += ["", "## Mean absolute error per test day, model / persistence", ""]
    days = sorted({day for entry in horizons.values() for day in entry["per_day"]})
    lines += ["| Horizon | " + " | ".join(days) + " |", "|---|" + "---|" * len(days)]
    for horizon, entry in horizons.items():
        cells = " | ".join(_day_cell(entry["per_day"].get(day)) for day in days)
        lines.append(f"| {horizon} min | {cells} |")
    for horizon, entry in horizons.items():
        if "importance" in entry:
            lines += ["", f"## Most useful features at {horizon} min (permutation importance)", ""]
            lines += ["| Feature | MAE increase, in bikes |", "|---|---|"]
            for name, value in list(entry["importance"].items())[:10]:
                lines.append(f"| {name} | {value:.3f} |")
    return "\n".join(lines) + "\n"


def _day_cell(values: dict[str, float] | None) -> str:
    # A day can be missing at long horizons: its forecasts would target times after the data.
    if values is None:
        return "n/a"
    return f"{values['model']:.2f} / {values['persistence']:.2f}"


def _per_day(
    test_rows: pl.DataFrame, predictions: dict[str, pl.Series]
) -> dict[str, dict[str, float]]:
    frame = test_rows.select(
        pl.col("time").dt.convert_time_zone(PARIS_TIME_ZONE).dt.date().alias("day"), "target"
    ).with_columns(
        predictions["model"].alias("model"), predictions["persistence"].alias("persistence")
    )
    rows = (
        frame.group_by("day")
        .agg(
            (pl.col("model") - pl.col("target")).abs().mean().alias("model"),
            (pl.col("persistence") - pl.col("target")).abs().mean().alias("persistence"),
        )
        .sort("day")
    )
    return {
        row["day"].isoformat(): {"model": row["model"], "persistence": row["persistence"]}
        for row in rows.iter_rows(named=True)
    }


def _importance(model: Any, test_rows: pl.DataFrame, sample: int) -> dict[str, float]:
    rows = test_rows.sample(n=min(sample, test_rows.height), seed=0)
    change = (rows.get_column("target") - rows.get_column("bikes")).cast(pl.Float64).to_numpy()
    result = permutation_importance(
        model,
        feature_matrix(rows),
        change,
        n_repeats=3,
        random_state=0,
        scoring="neg_mean_absolute_error",
    )
    values = {
        name: float(value) for name, value in zip(FEATURES, result.importances_mean, strict=True)
    }
    return dict(sorted(values.items(), key=lambda item: item[1], reverse=True))


def _resolve_source(value: str) -> Path:
    for candidate in (ARCHIVES_DIR / value, Path(value)):
        if (candidate / "daily").is_dir():
            return candidate
    raise SystemExit(
        f"unknown source {value!r}: import it first with `velib-archives import {value}`"
    )
```

Ajouter sous `[project.scripts]` de `pyproject.toml` la ligne `velib-forecast = "velib.forecast.cli:main"`, puis
`uv sync --all-groups`.

- [ ] **Step 3: Vérifier et committer**

Run: `uv run --group ml pytest -q`
Expected: PASS.

```bash
git add pyproject.toml uv.lock src/velib/forecast/cli.py tests/integration/test_forecast_cli.py
uv run --group ml pre-commit run --all-files
git commit -q -m "feat: evaluate forecasts with velib-forecast" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 4: Évaluation réelle et publication

**Files:**
- Create: `docs/forecasting.md`
- Modify: `mkdocs.yml` (entrée `Forecasting: forecasting.md` dans `nav`), `README.md` (section « Forecasting »)

- [ ] **Step 1: Lancer l'évaluation sur les deux semaines Kaggle**

```bash
uv run --group ml velib-forecast evaluate --source kaggle
```

Expected: `data/forecast/kaggle/report.md` avec 5 horizons ; le modèle bat la meilleure référence à chaque horizon.

- [ ] **Step 2: Publier les résultats**

Écrire `docs/forecasting.md` (méthode, tableau des MAE et du gain, variables les plus utiles, limites, attribution
CC BY-SA 4.0 du jeu Kaggle) avec les chiffres de `report.md`, ajouter la page à `nav` et une section au README,
construire le site en mode strict, puis fusionner et pousser après le contrôle d'identité.
