"""Command line of the operator analyses: velib-ops report."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from velib import DATA_DIR
from velib.archives import resolve_source
from velib.db import connect
from velib.ops.report import analyse, map_payload, render_map_html, render_markdown

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the velib-ops command line.

    Args:
        argv: Arguments, sys.argv[1:] when None.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(
        description="Find rebalancing operations and out-of-service bikes in the snapshots."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="Write the report and the map of a source.")
    report.add_argument(
        "--source",
        default="velib-data",
        help="velib-data, an imported archive such as kaggle, or a directory.",
    )
    report.add_argument(
        "--out", type=Path, default=None, help="Report directory, data/ops/<source> by default."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    label = Path(args.source).name
    out: Path = args.out or DATA_DIR / "ops" / label
    findings = analyse(connect(resolve_source(args.source)))
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render_markdown(findings, label), encoding="utf-8")
    title = f"Régulation et pannes ({label})"
    (out / "map.html").write_text(render_map_html(map_payload(findings), title), encoding="utf-8")
    logger.info("wrote %s and %s", out / "report.md", out / "map.html")
    return 0
