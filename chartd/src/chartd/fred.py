"""FRED (St. Louis Fed) series: the graph CSV, keyless. Plain requests are refused; a Chrome fingerprint is not.

A series is one value per observation date. As bars, each value is a daily bar whose open, high, low and
close are that value (the US Treasury yields: US10Y is DGS10). As an economic series, the dates and values.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import time

from curl_cffi.requests import AsyncSession

from .yahoo import Chart, NoData, YahooError

CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
META = {"exchangeTimezoneName": "America/New_York", "instrumentType": "ECONOMIC", "fullExchangeName": "FRED"}
#: Seconds a downloaded series is reused.
TTL = 3600.0


class FredError(YahooError):
    pass


class Fred:
    def __init__(self) -> None:
        self._s: AsyncSession | None = None
        self._cache: dict[str, tuple[float, list[tuple[dt.date, float]]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        if self._s is not None:
            await self._s.close()

    async def observations(self, series_id: str) -> list[tuple[dt.date, float]]:
        """(date, value) for every observation with a value (FRED writes a missing one as ".")."""
        lock = self._locks.setdefault(series_id, asyncio.Lock())
        async with lock:
            hit = self._cache.get(series_id)
            if hit and time.time() - hit[0] < TTL:
                return hit[1]
            if self._s is None:
                self._s = AsyncSession(impersonate="chrome", timeout=40)
            last = None
            for attempt in range(3):
                try:
                    r = await self._s.get(CSV, params={"id": series_id})
                except Exception as e:
                    last = e
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code == 404:
                    raise NoData(f"FRED has no series {series_id}")
                if r.status_code != 200 or r.text.lstrip().startswith("<"):
                    last = f"HTTP {r.status_code}"
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                rows = parse_csv(r.text)
                self._cache[series_id] = (time.time(), rows)
                return rows
            raise FredError(f"FRED did not answer for {series_id} ({last})")

    async def chart(self, series_id: str, interval: str, period1: int, period2: int | None = None) -> Chart:
        if interval != "1d":
            raise NoData(f"{series_id}: FRED has daily values only")
        rows = await self.observations(series_id)
        # Noon New York: the bar keys on that date's midnight whatever the zone offset. One value a day: each
        # candle runs from the previous day's value to this one (a close-to-close candle; its source label says so).
        bars, prev = [], None
        for d, v in rows:
            o = v if prev is None else prev
            t = int(dt.datetime.combine(d, dt.time(12), dt.timezone(dt.timedelta(hours=-5))).timestamp())
            bars.append((t, o, max(o, v), min(o, v), v, 0.0))
            prev = v
        return Chart([b for b in bars if b[0] >= period1], [], [], dict(META))


def parse_csv(text: str) -> list[tuple[dt.date, float]]:
    rd = csv.reader(io.StringIO(text))
    next(rd, None)  # observation_date,<ID>
    out = []
    for row in rd:
        if len(row) < 2 or row[1] in (".", ""):
            continue
        try:
            out.append((dt.date.fromisoformat(row[0]), float(row[1])))
        except ValueError:
            continue
    return out
