"""When to ask Yahoo for bars, how much, and how a new split is applied.

- First request for a series: daily from 1900 (all of it), intraday as far back as Yahoo serves
  (1m: 7 days, 5m/15m: 59 days, 1h: 729 days). Kept on disk and growing from then on.
- Later requests: only the tail (from a little before the last stored bar), at most once per TTL.
- Intraday asks the daily series first: the daily answer carries the splits. A split not seen before means
  Yahoo's prices were re-adjusted: the daily series is fetched again whole, stored intraday bars older than
  the split are rescaled, and each intraday window is fetched again.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .store import Row, Store
from .yahoo import EPOCH_1900, INTRADAY_DAYS, Chart

#: A source of bars: Yahoo or Binance (`chart(native_symbol, interval, period1)`).
Route = Callable[[str], tuple[Any, str]]

#: Store key -> Yahoo interval.
INTERVAL = {"1d": "1d", "60m": "1h", "15m": "15m", "5m": "5m", "1m": "1m"}
#: Seconds a fetched series is fresh.
TTL = {"1d": 600, "60m": 120, "15m": 60, "5m": 60, "1m": 30}
#: How far before the last stored bar a tail fetch starts (catches late corrections).
OVERLAP = {"1d": 10 * 86400, "60m": 3 * 86400, "15m": 2 * 86400, "5m": 2 * 86400, "1m": 86400}
NY = ZoneInfo("America/New_York")


def store_tf(tf: str) -> str | None:
    """The Market API's `tf` ("1d", "60m", "1h", "15m"...) -> the store key, or None when not served."""
    tf = tf.strip().lower()
    if tf in ("1h", "60m"):
        return "60m"
    return tf if tf in INTERVAL else None


def day_ms(t: int, tz: ZoneInfo) -> int:
    """A daily bar's Yahoo time (the session open) -> midnight of that trading day in the exchange zone, in ms."""
    d = dt.datetime.fromtimestamp(t, tz).date()
    return int(dt.datetime.combine(d, dt.time(), tz).timestamp() * 1000)


def session_end(meta: dict, tz: ZoneInfo) -> dt.time | None:
    """Time of day the regular session closes (from the answer's current trading period)."""
    end = (((meta.get("currentTradingPeriod") or {}).get("regular")) or {}).get("end")
    return dt.datetime.fromtimestamp(end, tz).time() if end else None


def sane(r: Row) -> Row:
    """A bar whose high is under its open or close, or whose low is over them (Yahoo sends some, mostly FX and
    softs), widened to cover them."""
    t, o, h, lo, c, v = r
    return (t, o, max(h, o, c), min(lo, o, c), c, v)


def rows_of(chart: Chart, tf: str, tz: ZoneInfo) -> list[Row]:
    return [sane(r) for r in _rows_of(chart, tf, tz)]


def _rows_of(chart: Chart, tf: str, tz: ZoneInfo) -> list[Row]:
    if tf == "1d":
        return [(day_ms(t, tz), o, h, lo, c, v) for t, o, h, lo, c, v in chart.bars]
    # Yahoo appends a stock's closing print as a bar of its own at the session's end (16:00 New York): no
    # volume, one price. It is not a bar of the chart's grid; drop it. (Futures and crypto trade on.)
    end = session_end(chart.meta, tz) if chart.meta.get("instrumentType") in ("EQUITY", "ETF", "INDEX", "MUTUALFUND") else None
    return [
        (t * 1000, o, h, lo, c, v)
        for t, o, h, lo, c, v in chart.bars
        if not (end and v == 0 and o == h == lo == c and dt.datetime.fromtimestamp(t, tz).time() >= end)
    ]


def unadjusted(rows: list[Row], splits: list[tuple[int, float, float]], tz: ZoneInfo) -> list[Row]:
    """Split-adjusted bars -> the prices as traded: a bar before a k-for-1 split is k times higher.

    A split takes effect on its day: every bar from that day's midnight (exchange zone) on trades split, the
    daily bar of the split day included (its key is that midnight, before Yahoo's split time at the open)."""
    if not splits:
        return rows
    cuts = [(day_ms(st, tz), num / den) for st, num, den in splits]
    out = []
    for r in rows:
        k = 1.0
        for cut, ratio in cuts:
            if r[0] < cut:
                k *= ratio
        out.append((r[0], r[1] * k, r[2] * k, r[3] * k, r[4] * k, r[5] / k) if k != 1.0 else r)
    return out


class Bars:
    def __init__(self, store: Store, route: Route) -> None:
        """`route(sym)` -> (the source holding `sym`, its name there)."""
        self.store, self.route = store, route
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, sym: str, tf: str) -> asyncio.Lock:
        return self._locks.setdefault((sym, tf), asyncio.Lock())

    def tz(self, sym: str) -> ZoneInfo:
        m = self.store.meta(sym)
        try:
            return ZoneInfo(m["tz"]) if m and m["tz"] else NY
        except Exception:
            return NY

    async def ensure(self, sym: str, tf: str, *, force: bool = False) -> None:
        """Make the stored series of `sym` fresh (raises yahoo.NoData for an unknown symbol)."""
        if tf != "1d":
            await self.ensure(sym, "1d")
        async with self._lock(sym, tf):
            s = self.store.series(sym, tf)
            now = time.time()
            if s and not force and now - s.fetched_at < TTL[tf]:
                return
            if tf == "1d":
                p1 = EPOCH_1900
            else:
                p1 = int(now - INTRADAY_DAYS[INTERVAL[tf]] * 86400)
            tail = bool(s and s.last_t and not force)
            if tail:
                p1 = max(p1, s.last_t // 1000 - OVERLAP[tf])
            src, native = self.route(sym)
            chart = await src.chart(native, INTERVAL[tf], p1)
            m = chart.meta
            if tf == "1d":
                self.store.set_meta(sym, m.get("exchangeTimezoneName"), m.get("longName") or m.get("shortName"),
                                    m.get("currency"), m.get("fullExchangeName"))
            rows = rows_of(chart, tf, self.tz(sym))
            if tf == "1d":
                new = self.store.add_events(sym, chart.splits, chart.dividends)
                if tail and new:
                    # A split since the last fetch: every older price Yahoo sends is now adjusted for it.
                    full = await src.chart(native, "1d", EPOCH_1900)
                    self.store.add_events(sym, full.splits, full.dividends)
                    self.store.replace_series(sym, "1d", rows_of(full, "1d", self.tz(sym)))
                    for itf in self.store.intraday_tfs(sym):
                        for st, num, den in new:
                            self.store.rescale_before(sym, itf, day_ms(st, self.tz(sym)), num / den)
                        self.store.mark_fetched(sym, itf, 0)  # stale: the next ask fetches its tail
                    self.store.mark_fetched(sym, "1d", now)
                    return
            self.store.upsert_bars(sym, tf, rows)
            self.store.mark_fetched(sym, tf, now)

    async def read(self, sym: str, tf: str, t0: int, t1: int, adjusted: bool) -> list[Row]:
        await self.ensure(sym, tf)
        rows = self.store.bars(sym, tf, t0, t1)
        return rows if adjusted else unadjusted(rows, self.store.splits(sym), self.tz(sym))
