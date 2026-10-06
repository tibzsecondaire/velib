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
