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
