"""Write a map that replays the fill rate of every station over a period, slot by slot.

Usage:
    uv run python scripts/replay_map.py --source kaggle
    uv run python scripts/replay_map.py --source velib-data --start 2026-10-06 --slot 30m
    uv run python scripts/replay_map.py --source kaggle --serve

The page opens straight from the file in Safari. Chromium-based browsers refuse to start the
map worker from a local file: --serve opens the page through a local web server instead.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import logging
import webbrowser
from datetime import date
from pathlib import Path
from urllib.parse import quote

import polars as pl

from velib import DATA_DIR
from velib.archives import resolve_source
from velib.dataset import load_period, load_stations
from velib.heatmap import PARIS_TIME_ZONE
from velib.replay import build_replay, render_replay_html

logger = logging.getLogger("replay_map")


def serve(page: Path) -> None:
    """Serves the page on localhost and opens it in the browser, until Ctrl+C."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=page.parent)
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/{quote(page.name)}"
        logger.info("serving %s, press Ctrl+C to stop", url)
        webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


def main() -> None:
    """Writes the replay page of the requested period to data/figures/."""
    parser = argparse.ArgumentParser(description="Replay Vélib' fill rates on a map.")
    parser.add_argument(
        "--source",
        default="kaggle",
        help="velib-data, an imported archive such as kaggle or lovasoa, or a directory.",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="First Paris day.")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Last Paris day.")
    parser.add_argument("--slot", default="1h", help="Slot length, for example 30m or 1h.")
    parser.add_argument(
        "--serve", action="store_true", help="Open the page through a local web server."
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    source = resolve_source(args.source)
    try:
        frame = load_period(source=source, start=args.start, end=args.end)
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from error
    local_day = pl.col("fetched_at").dt.convert_time_zone(PARIS_TIME_ZONE).dt.date()
    first, last = frame.select(local_day.min(), local_day.max().alias("last")).row(0)
    label = Path(args.source).name
    payload = build_replay(frame, load_stations(source=source), slot=args.slot)
    title = f"Vélib' du {first} au {last}, créneaux de {args.slot} ({label})"
    output = DATA_DIR / "figures" / f"replay_{label}_{first}_{last}_{args.slot}.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_replay_html(payload, title), encoding="utf-8")
    logger.info(
        "wrote %s: %d stations, %d slots", output, len(payload["stations"]), len(payload["times"])
    )
    if args.serve:
        serve(output)


if __name__ == "__main__":
    main()
