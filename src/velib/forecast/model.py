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


def train(examples: pl.DataFrame, *, max_iter: int = 300) -> HistGradientBoostingRegressor:
    """Fits a model of the change of bikes between t and t + horizon.

    Args:
        examples: Training examples with FEATURES, bikes and target.
        max_iter: Number of boosting iterations.

    Returns:
        The fitted model.
    """
    model = HistGradientBoostingRegressor(
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
