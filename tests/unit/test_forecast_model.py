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
