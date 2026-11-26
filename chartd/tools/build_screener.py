"""Build the screener's state and closes files from the store now (what the nightly run ends with).

Usage: uv run python tools/build_screener.py [--out DIR]   (default: chartd's data folder)
Prints the time taken and the peak memory.
"""
import argparse
import asyncio
import json
import resource
import time
from pathlib import Path

from chartd.screener import build
from chartd.server import App, data_dir
from chartd.store import Store
from chartd.yahoo import Yahoo


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = data_dir()
    app = App(Store(d / "bars.sqlite"), Yahoo(), d)
    await app.load_listings()
    fund = await app.market.fundamentals()
    tickers = app.market.us_tickers()
    t0 = time.time()
    s = build(app.store, tickers, lambda x: app.bars.tz(x), fund, Path(a.out) if a.out else d)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(json.dumps({**s, "seconds": round(time.time() - t0, 1), "peak_mb": round(peak)}))
    await app.yahoo.close()


asyncio.run(main())
