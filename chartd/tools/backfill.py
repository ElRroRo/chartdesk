"""Fetch the daily history of many listings into the store (the screener's base), measuring Yahoo's tolerance.

Usage: uv run python tools/backfill.py [--limit N] [--concurrency C] [--types stock,etf]
Reports requests per second, errors by kind, and stops early when Yahoo starts refusing (HTTP 429 streak).
"""
import argparse
import asyncio
import collections
import time
from pathlib import Path

from chartd.bars import Bars
from chartd.server import App, data_dir
from chartd.store import Store
from chartd.yahoo import NoData, Yahoo, YahooError


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--types", default="stock,etf")
    a = ap.parse_args()
    d = data_dir()
    app = App(Store(d / "bars.sqlite"), Yahoo(concurrency=a.concurrency), d)
    await app.load_listings()
    types = set(a.types.split(","))
    todo = [t for t, li in sorted(app.listings.items()) if li.type in types and li.exchange != "BIST"
            and app.store.series(t, "1d") is None][: a.limit]
    print(f"{len(todo)} to fetch, concurrency {a.concurrency}", flush=True)
    errs = collections.Counter()
    done = 0
    t0 = time.time()
    sem = asyncio.Semaphore(a.concurrency)

    async def one(t):
        nonlocal done
        async with sem:
            try:
                await app.bars.ensure(t, "1d")
            except NoData:
                errs["no data"] += 1
            except YahooError as e:
                errs[str(e)[:60]] += 1
            done += 1
            if done % 50 == 0:
                print(f"{done} in {time.time() - t0:.0f}s ({done / (time.time() - t0):.1f}/s) errors {dict(errs)}", flush=True)

    await asyncio.gather(*(one(t) for t in todo))
    el = time.time() - t0
    print(f"done {done} in {el:.0f}s = {done / el:.2f}/s; errors {dict(errs)}", flush=True)
    await app.yahoo.close()


asyncio.run(main())
