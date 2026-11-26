"""Borsa Istanbul stocks: Yahoo's bar shapes at Is Yatirim's price level, and Is Yatirim's own corporate actions.

Why: Yahoo's BIST history is adjusted inconsistently (CCOLA's 11-for-1 bonus issue of 13 Aug 2024 is applied to
prices from 1 Aug only; a 2006 bonus issue is missing), while Is Yatirim (HisseTekil) carries each day's as-traded
close / low / high / turnover (HG_*), its fully adjusted close (HGDG_KAPANIS) and the share capital (SERMAYE).

- Corporate actions: the ratio as-traded / adjusted steps at every event by Is Yatirim's factor. A step within five
  trading days of a capital increase is a bonus or rights issue (a split for the chart, at Is Yatirim's factor); any
  other step is a dividend, its amount the previous close x (f - 1) / f.
- Daily bars: close, high, low from Is Yatirim; the open from Yahoo's bar of the day scaled to Is Yatirim's close
  (the previous close when Yahoo has no bar); volume in shares = turnover / average price.
- Intraday bars (Yahoo only has them): each day scaled so its last close is that day's daily close.

Is Yatirim's rows come in no fixed order (sort them), and a proxy error page arrives as HTTP 200 (check the body).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession

from .yahoo import Chart, NoData, Yahoo, YahooError

ISY = "https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
IST = ZoneInfo("Europe/Istanbul")
META = {"exchangeTimezoneName": "Europe/Istanbul", "instrumentType": "EQUITY", "currency": "TRY", "fullExchangeName": "Borsa Istanbul"}


class IsYatirimError(YahooError):
    pass


def day_of(s: str) -> dt.date:
    d, m, y = s.split("-")
    return dt.date(int(y), int(m), int(d))


def at10(d: dt.date) -> int:
    """10:00 Istanbul on `d` (Unix s): the session open, the store keys the bar on that day."""
    return int(dt.datetime.combine(d, dt.time(10), IST).timestamp())


def actions(rows: list[dict]) -> tuple[list[tuple[dt.date, float]], list[tuple[dt.date, float]]]:
    """(splits as (date, factor), dividends as (ex date, as-traded amount)) from sorted Is Yatirim rows."""
    ok = [z for z in rows if z.get("HG_KAPANIS") and z.get("HGDG_KAPANIS")]
    ratio = [z["HG_KAPANIS"] / z["HGDG_KAPANIS"] for z in ok]
    steps = []  # (index, factor)
    for i in range(1, len(ok)):
        f = ratio[i - 1] / ratio[i]
        if f > 1.0005:
            steps.append((i, f))
    cap = [z.get("SERMAYE") or 0.0 for z in ok]
    split_at: dict[int, float] = {}
    for i in range(1, len(ok)):
        if cap[i - 1] and cap[i] and cap[i] / cap[i - 1] > 1.02:
            # The capital increase's own step: the largest within five trading days of it.
            near = [(j, f) for j, f in steps if abs(j - i) <= 5 and f > 1.02]
            if near:
                j, f = max(near, key=lambda x: x[1])
                split_at[j] = round(f, 4)
    splits = [(day_of(ok[j]["HGDG_TARIH"]), f) for j, f in sorted(split_at.items())]
    divs = [
        (day_of(ok[j]["HGDG_TARIH"]), ok[j - 1]["HG_KAPANIS"] * (f - 1) / f)
        for j, f in steps
        if j not in split_at and f < 1.5
    ]
    return splits, divs


def daily_rows(rows: list[dict], yahoo_bars: dict[dt.date, tuple]) -> list[tuple[dt.date, float, float, float, float, float]]:
    """As-traded daily bars: (date, o, h, l, c, shares)."""
    out = []
    prev_c = None
    for z in rows:
        c, h, lo = z.get("HG_KAPANIS"), z.get("HG_MAX"), z.get("HG_MIN")
        if not c:
            continue
        d = day_of(z["HGDG_TARIH"])
        y = yahoo_bars.get(d)
        o = y[0] * c / y[3] if y and y[3] else (prev_c or c)
        h = max(h or c, o, c)
        lo = min(lo or c, o, c)
        aof, turnover = z.get("HG_AOF"), z.get("HG_HACIM")
        v = turnover / aof if turnover and aof else 0.0
        out.append((d, o, h, lo, c, v))
        prev_c = c
    return out


class IsYatirim:
    def __init__(self) -> None:
        self._s: AsyncSession | None = None
        self._sem = asyncio.Semaphore(2)

    async def close(self) -> None:
        if self._s is not None:
            await self._s.close()

    async def _range(self, code: str, start: dt.date, end: dt.date) -> list[dict]:
        if self._s is None:
            self._s = AsyncSession(impersonate="chrome", timeout=60)
        params = {"hisse": code, "startdate": start.strftime("%d-%m-%Y"), "enddate": end.strftime("%d-%m-%Y")}
        last = None
        for attempt in range(4):
            async with self._sem:
                try:
                    r = await self._s.get(ISY, params=params)
                except Exception as e:
                    last = e
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
            head = r.text[:200].lstrip().lower()
            if r.status_code >= 500 or head.startswith("<") or "proxy error" in head:
                last = f"HTTP {r.status_code} / proxy error page"
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise IsYatirimError(f"Is Yatirim {code}: HTTP {r.status_code}")
            return (r.json() or {}).get("value") or []
        raise IsYatirimError(f"Is Yatirim did not answer for {code} ({last})")

    async def history(self, code: str, start: dt.date) -> list[dict]:
        """Rows from `start` to today, sorted by date. Falls back to five-year slices when the whole range fails."""
        end = dt.datetime.now(IST).date()
        try:
            rows = await self._range(code, start, end)
        except IsYatirimError:
            rows = []
            for y0 in range(max(start.year, 1990), end.year + 1, 5):
                a, b = max(start, dt.date(y0, 1, 1)), min(end, dt.date(y0 + 4, 12, 31))
                rows += await self._range(code, a, b)
        seen, out = set(), []
        for z in sorted(rows, key=lambda z: day_of(z["HGDG_TARIH"])):
            if z["HGDG_TARIH"] not in seen:
                seen.add(z["HGDG_TARIH"])
                out.append(z)
        return out


class BistSource:
    """The bar source of a BIST stock (`chart(native, interval, period1)` like Yahoo's)."""

    def __init__(self, yahoo: Yahoo, isy: IsYatirim | None = None) -> None:
        self.yahoo, self.isy = yahoo, isy or IsYatirim()
        #: code -> {date: split-adjusted close}, for scaling intraday bars.
        self._closes: dict[str, dict[dt.date, float]] = {}

    async def close(self) -> None:
        await self.isy.close()

    async def chart(self, native: str, interval: str, period1: int, period2: int | None = None) -> Chart:
        code = native.removesuffix(".IS")
        if interval == "1d":
            return await self._daily(code, native, period1)
        return await self._intraday(code, native, interval, period1)

    async def _daily(self, code: str, native: str, period1: int) -> Chart:
        start = max(dt.date(1990, 1, 1), dt.datetime.fromtimestamp(max(period1, 631152000), IST).date() - dt.timedelta(days=7))
        rows, ych = await asyncio.gather(self.isy.history(code, start), self._yahoo(native, "1d", at10(start)))
        if not rows:
            raise NoData(f"{code}: Is Yatirim has no prices")
        ybars = {dt.datetime.fromtimestamp(t, IST).date(): (o, h, lo, c, v) for t, o, h, lo, c, v in (ych.bars if ych else [])}
        splits, divs = actions(rows)
        raw = daily_rows(rows, ybars)
        later = lambda d: _prod(f for sd, f in splits if d < sd)
        bars = []
        closes = self._closes.setdefault(code, {})
        for d, o, h, lo, c, v in raw:
            k = later(d)
            bars.append((at10(d), o / k, h / k, lo / k, c / k, v * k))
            closes[d] = c / k
        meta = dict(META)
        if ych:
            meta["longName"] = ych.meta.get("longName")
        return Chart(
            bars,
            [(at10(d), f, 1.0) for d, f in splits],
            [(at10(d), amount / later(d)) for d, amount in divs],
            meta,
        )

    async def _intraday(self, code: str, native: str, interval: str, period1: int) -> Chart:
        ych = await self.yahoo.chart(native, interval, period1)
        if code not in self._closes:
            await self._daily(code, native, period1 - 10 * 86400)
        closes = self._closes.get(code, {})
        last_of_day: dict[dt.date, float] = {}
        for t, _o, _h, _lo, c, _v in ych.bars:
            last_of_day[dt.datetime.fromtimestamp(t, IST).date()] = c
        bars = []
        for t, o, h, lo, c, v in ych.bars:
            d = dt.datetime.fromtimestamp(t, IST).date()
            k = closes[d] / last_of_day[d] if d in closes and last_of_day.get(d) else 1.0
            bars.append((t, o * k, h * k, lo * k, c * k, v / k if k else v))
        return Chart(bars, [], [], {**ych.meta, **{k: META[k] for k in ("exchangeTimezoneName",)}})

    async def _yahoo(self, native: str, interval: str, period1: int) -> Chart | None:
        try:
            return await self.yahoo.chart(native, interval, period1)
        except YahooError:
            return None  # the open falls back to the previous close


def _prod(xs) -> float:
    p = 1.0
    for x in xs:
        p *= x
    return p
