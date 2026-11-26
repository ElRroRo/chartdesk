"""The whole US market for the screener: Nasdaq's company file, the nightly update and build, the live snapshot.

- Nasdaq's public screener file (api.nasdaq.com, one request): market cap, last price, sector and industry of
  every listed US stock; shares = market cap / last price. Once a day, the last copy kept.
- Nightly, after the New York close: today's daily bar of every US ticker in the store from batch quotes (50 a
  request); a ticker whose previous close no longer matches the stored one had a split (or a correction) and is
  fetched again; tickers not refreshed for a week are topped up a few hundred a night; then the screener's state
  and closes files are built (screener.py).
- Live: while the screener asks for the whole-market snapshot (it polls), batch quotes cycle through the market and
  the answer is rebuilt after each pass; idle a minute, the cycle stops.
- First fill: a new install has only what was charted. The screener asks before it downloads the market's daily
  history (~11,700 tickers, ~2 GB, ~45 min; `fill()`); until the market is filled, no state is built (the app reads
  a state once per session, so a partial one would stay until the next day). An interrupted fill resumes.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
import time
from typing import TYPE_CHECKING

from .bars import NY, day_ms
from .screener import build, us_holidays
from .yahoo import NoData, YahooError

if TYPE_CHECKING:
    from .server import App

NASDAQ = "https://api.nasdaq.com/api/screener/stocks"
NIGHTLY_AFTER = dt.time(16, 40)
LIVE_IDLE = 60.0
FILL_CONCURRENCY = 6
#: The market counts as filled when this share of its US stocks and ETFs has daily history.
FILLED_SHARE = 0.9


def trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in us_holidays(d.year)


def num(s) -> float | None:
    try:
        return float(str(s).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


class Market:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.dir = app.dir
        self.fund: dict[str, dict] = {}
        self.live_asked = 0.0
        self.live_body: bytes | None = None
        self.live_at = 0.0
        self._live_task: asyncio.Task | None = None
        self.building = False
        self._fill_task: asyncio.Task | None = None
        self.fill_done = 0
        self.fill_total = 0
        self.fill_started = 0.0
        self.fill_errors = 0

    # ── Nasdaq's company file ──
    async def fundamentals(self) -> dict[str, dict]:
        path = self.dir / "nasdaq_screener.json"
        today = dt.datetime.now(NY).date()
        fresh = path.exists() and dt.datetime.fromtimestamp(path.stat().st_mtime, NY).date() == today
        if not fresh:
            try:
                s = await self.app.yahoo._session()
                r = await s.get(NASDAQ, params={"tableonly": "true", "limit": "25", "download": "true"},
                                headers={"Accept": "application/json"})
                rows = r.json()["data"]["rows"]
                path.write_text(json.dumps(rows))
            except Exception as e:
                print(f"[chartd] nasdaq screener file: {e}", flush=True)
        if not path.exists():
            return {}
        out = {}
        for z in json.loads(path.read_text()):
            t = str(z.get("symbol", "")).strip().upper().replace("/", ".").replace("^", ".")
            cap, last = num(z.get("marketCap")), num(z.get("lastsale"))
            out[t] = {"shares": cap / last if cap and last else None, "sector": z.get("sector") or None,
                      "industry": z.get("industry") or None}
        self.fund = out
        return out

    def us_listings(self) -> list[str]:
        return sorted(t for t, li in self.app.listings.items()
                      if li.market == "stocks" and li.exchange != "BIST" and li.type in ("stock", "etf"))

    def filled(self) -> bool:
        if (self.dir / "screener_filled").exists():
            return True
        listed = self.us_listings()
        return bool(listed) and len(set(self.app.store.symbols_with("1d")) & set(listed)) >= FILLED_SHARE * len(listed)

    def fill_progress(self) -> dict:
        running = self._fill_task is not None and not self._fill_task.done()
        el = time.time() - self.fill_started if running else 0
        rate = self.fill_done / el if el > 5 and self.fill_done else None
        left = (self.fill_total - self.fill_done) / rate if rate else None
        return {"filled": self.filled(), "running": running, "building": self.building, "done": self.fill_done,
                "total": self.fill_total, "errors": self.fill_errors, "secondsLeft": round(left) if left else None,
                "stateAsof": a.isoformat() if (a := self.state_asof()) else None}

    def start_fill(self) -> None:
        if self._fill_task is None or self._fill_task.done():
            self._fill_task = asyncio.create_task(self.fill())

    async def fill(self) -> None:
        """Download the daily history of every US stock and ETF the store lacks, then run the nightly build."""
        await self.app.load_listings()
        have = set(self.app.store.symbols_with("1d"))
        todo = [t for t in self.us_listings() if t not in have]
        self.fill_total, self.fill_done, self.fill_errors, self.fill_started = len(todo), 0, 0, time.time()
        sem = asyncio.Semaphore(FILL_CONCURRENCY)

        async def one(t: str) -> None:
            async with sem:
                try:
                    await self.app.bars.ensure(t, "1d")
                except (NoData, YahooError):
                    self.fill_errors += 1
                self.fill_done += 1

        try:
            await asyncio.gather(*(one(t) for t in todo))
            (self.dir / "screener_filled").write_text(dt.datetime.now(NY).isoformat())
            print(f"[chartd] market filled: {self.fill_done} tickers, {self.fill_errors} without data", flush=True)
            await self.nightly()
        except Exception as e:  # noqa: BLE001 (reported; a later fill resumes)
            print(f"[chartd] market fill: {e}", flush=True)

    def us_tickers(self) -> list[str]:
        """US stocks and ETFs with daily bars in the store."""
        have = set(self.app.store.symbols_with("1d"))
        return sorted(t for t, li in self.app.listings.items()
                      if li.market == "stocks" and li.exchange != "BIST" and li.type in ("stock", "etf") and t in have)

    # ── nightly ──
    async def nightly(self) -> dict:
        await self.app.load_listings()
        tickers = self.us_tickers()
        appended, refetch = 0, []
        for i in range(0, len(tickers), 50):
            chunk = tickers[i : i + 50]
            ymap = {self.app.route(t)[1]: t for t in chunk}
            try:
                got = await self.app.yahoo.quotes(list(ymap))
            except YahooError as e:
                print(f"[chartd] nightly quotes: {e}", flush=True)
                continue
            for y, q in got.items():
                t = ymap.get(y)
                if not t or not q.get("regularMarketTime") or q.get("regularMarketPrice") is None:
                    continue
                key = day_ms(int(q["regularMarketTime"]), NY)
                last2 = self.app.store.last_bars(t, "1d", 2)
                pc = q.get("regularMarketPreviousClose")
                prev = [r for r in last2 if r[0] < key]
                if prev and pc and abs(prev[-1][4] / pc - 1) > 0.005:
                    refetch.append(t)  # the stored history no longer matches Yahoo's: a split or a correction
                    continue
                o, h, lo, c = (q.get(k) for k in ("regularMarketOpen", "regularMarketDayHigh", "regularMarketDayLow", "regularMarketPrice"))
                if None in (o, h, lo, c):
                    continue
                self.app.store.upsert_bars(t, "1d", [(key, o, max(h, o, c), min(lo, o, c), c, float(q.get("regularMarketVolume") or 0))])
                appended += 1
        # Top-ups: the mismatched, then the longest unrefreshed (a week or more), a few hundred a night.
        week_ago = time.time() - 7 * 86400
        stale = [t for t in tickers if (s := self.app.store.series(t, "1d")) and s.fetched_at < week_ago]
        todo = list(dict.fromkeys(refetch + stale[:400]))
        sem = asyncio.Semaphore(4)

        async def top(t):
            async with sem:
                self.app.store.mark_fetched(t, "1d", 0)
                try:
                    await self.app.bars.ensure(t, "1d")
                except YahooError:
                    pass

        await asyncio.gather(*(top(t) for t in todo))
        summary = await self.build()
        summary.update({"appended": appended, "refetched": len(refetch), "topped_up": len(todo)})
        (self.dir / "screener_nightly.json").write_text(json.dumps({**summary, "at": dt.datetime.now(NY).isoformat()}))
        print(f"[chartd] nightly: {summary}", flush=True)
        return summary

    async def build(self) -> dict:
        self.building = True
        try:
            fund = await self.fundamentals()
            tickers = self.us_tickers()
            # A daemon thread: a stop of chartd never waits for a build (a minute of numpy over the market).
            loop = asyncio.get_running_loop()
            fut = loop.create_future()

            def work():
                try:
                    r = build(self.app.store, tickers, lambda s: self.app.bars.tz(s), fund, self.dir)
                    loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(r))
                except Exception as e:  # noqa: BLE001 (handed to the awaiting coroutine)
                    loop.call_soon_threadsafe(lambda: fut.done() or fut.set_exception(e))

            threading.Thread(target=work, name="screener-build", daemon=True).start()
            return await fut
        finally:
            self.building = False

    def state_asof(self) -> dt.date | None:
        p = self.dir / "screener_nightly.json"
        try:
            return dt.date.fromisoformat(json.loads(p.read_text())["asof"])
        except Exception:
            return None

    async def scheduler(self) -> None:
        """After each trading day's close (16:40 New York), the nightly run; at start, a build when the state is
        older than the last session."""
        await asyncio.sleep(20)
        while True:
            try:
                now = dt.datetime.now(NY)
                last = now.date() if trading_day(now.date()) and now.time() >= NIGHTLY_AFTER else _previous_session(now.date())
                asof = self.state_asof()
                # Not before the market is filled: the app keeps the first state it reads for the whole session.
                if (asof is None or asof < last) and not self.building and self.filled():
                    await self.nightly()
            except Exception as e:
                print(f"[chartd] nightly failed: {e}", flush=True)
            await asyncio.sleep(600)

    # ── live whole-market snapshot ──
    def asked(self) -> None:
        self.live_asked = time.time()
        if self._live_task is None or self._live_task.done():
            self._live_task = asyncio.create_task(self._live())

    async def _live(self) -> None:
        while time.time() - self.live_asked < LIVE_IDLE:
            tickers = self.us_tickers()
            snaps = []
            for i in range(0, len(tickers), 50):
                chunk = tickers[i : i + 50]
                got = await self.app.quotes(chunk)
                snaps += [self.app.snap(t, got[t]) for t in chunk if t in got]
                if time.time() - self.live_asked > LIVE_IDLE:
                    return
            self.live_body = json.dumps({"tickers": snaps}).encode()
            self.live_at = time.time()
            await asyncio.sleep(5)


def _previous_session(d: dt.date) -> dt.date:
    p = d - dt.timedelta(days=1)
    while not trading_day(p):
        p -= dt.timedelta(days=1)
    return p
