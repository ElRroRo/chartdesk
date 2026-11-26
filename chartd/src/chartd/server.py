"""chartd's HTTP server: OpenTrader's Market API on 127.0.0.1:8798, answered from Yahoo through the bar store.

Routes the app's core calls (crates/core/src/data/market_rest): bars, snapshot (one, a list, the whole
store), tickers (details, prefix list, renames), catalog, splits, dividends, daily/<date>, and the daily
archive (history/v1/daily/<ticker>). Anything else answers 404 NOT_FOUND, which the app shows as "not
available" rather than an error.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

from aiohttp import web

from .bars import NY, Bars, store_tf
from .store import Store
from .agent import AgentRelay
from .alerts import Alerts, desktop_notify
from .binance import Binance
from .bist import BistSource
from .econ import Econ, catalog as econ_catalog
from .fred import Fred
from .market import Market
from .pyind import PyIndicators
from . import spread as sp
from .symbols import (YIELDS, Listing, base, crypto_listings, index_listings, is_index, other_listings, parse_kap,
                      parse_nasdaqlisted, parse_otherlisted, to_yahoo)
from .yahoo import NoData, Yahoo, YahooError

KAP = "https://www.kap.org.tr/tr/api/company/items/IGS/A"
LISTS = {
    "nasdaqlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "otherlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
QUOTE_TTL = 15.0


def data_dir() -> Path:
    """CHARTD_DATA (the desktop app sets it), else the platform's place for app data."""
    if os.environ.get("CHARTD_DATA"):
        return Path(os.environ["CHARTD_DATA"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Chart Desk" / "chartd"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chart Desk" / "chartd"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "chartdesk" / "chartd"


def err(code: str, msg: str, status: int) -> web.Response:
    return web.json_response({"error": {"code": code, "message": msg}}, status=status)


def ms_of(s: str, end: bool = False, tz=NY) -> int:
    """The Market API's from/to: Unix ms, or a date in the exchange's zone (`to` then means the end of that day)."""
    if s.lstrip("-").isdigit() and len(s.lstrip("-")) > 9:
        return int(s)
    d = dt.date.fromisoformat(s)
    t = int(dt.datetime.combine(d + dt.timedelta(days=int(end)), dt.time(), tz).timestamp() * 1000)
    return t - 1 if end else t


def cols(rows) -> dict:
    return {k: [r[i] for r in rows] for i, k in enumerate("tohlcv")}


class App:
    def __init__(self, store: Store, yahoo: Yahoo, dir: Path, binance: Binance | None = None) -> None:
        self.store, self.yahoo, self.dir = store, yahoo, dir
        self.binance = binance or Binance()
        self.fred = Fred()
        self.bist_src = BistSource(yahoo)
        self.econ = Econ(self.fred)
        self.alerts = Alerts(dir, self.last_prices, desktop_notify)
        self.market = Market(self)
        self.py = PyIndicators()
        self.agent = AgentRelay(dir)
        self._stored_market: tuple[float, bytes] | None = None
        self.bars = Bars(store, self.route)
        self.listings: dict[str, Listing] = {}
        self.crypto: set[str] = set()
        self.bist: set[str] = set()
        self.listing_day: dt.date | None = None
        self._quotes: dict[str, tuple[float, dict]] = {}

    # ── the US listing (nasdaqtrader), once a day, the last copy kept for offline starts ──
    async def load_listings(self) -> None:
        today = dt.datetime.now(NY).date()
        if self.listing_day == today and self.listings:
            return
        texts = {}
        for f, url in LISTS.items():
            path = self.dir / f
            fresh = path.exists() and dt.datetime.fromtimestamp(path.stat().st_mtime, NY).date() == today
            if not fresh:
                try:
                    s = await self.yahoo._session()
                    r = await s.get(url)
                    if r.status_code == 200 and "File Creation Time" in r.text:
                        path.write_text(r.text)
                except Exception as e:  # offline: the last copy
                    print(f"[chartd] listing {f}: {e}", flush=True)
            if path.exists():
                texts[f] = path.read_text()
        us = parse_nasdaqlisted(texts.get("nasdaqlisted.txt", "")) + parse_otherlisted(texts.get("otherlisted.txt", ""))
        bist = parse_kap(await self.kap_items(today), {r.ticker for r in us})
        rows = index_listings() + other_listings() + crypto_listings(await self.crypto_pairs(today)) + us + bist
        self.listings = {}
        for r in rows:
            self.listings.setdefault(r.ticker, r)  # a crypto pair's name never hides a stock's (none collide today)
        self.crypto = {r.ticker for r in rows if r.type == "crypto"}
        self.bist = {r.ticker for r in bist}
        self.listing_day = today

    async def kap_items(self, today: dt.date) -> list[dict]:
        """KAP's roster of listed companies (Borsa Istanbul), once a day, the last copy kept."""
        path = self.dir / "kap_companies.json"
        fresh = path.exists() and dt.datetime.fromtimestamp(path.stat().st_mtime, NY).date() == today
        if not fresh:
            try:
                s = await self.yahoo._session()
                r = await s.get(KAP)
                if r.status_code == 200 and r.text.lstrip().startswith("["):
                    keep = [{"stockCode": e.get("stockCode"), "kapMemberTitle": e.get("kapMemberTitle")} for e in r.json()]
                    path.write_text(json.dumps(keep))
            except Exception as e:
                print(f"[chartd] KAP roster: {e}", flush=True)
        return json.loads(path.read_text()) if path.exists() else []

    async def crypto_pairs(self, today: dt.date) -> list[tuple[str, str, str]]:
        path = self.dir / "binance_pairs.json"
        fresh = path.exists() and dt.datetime.fromtimestamp(path.stat().st_mtime, NY).date() == today
        if not fresh:
            try:
                path.write_text(json.dumps(await self.binance.pairs()))
            except Exception as e:
                print(f"[chartd] binance pairs: {e}", flush=True)
        return [tuple(x) for x in json.loads(path.read_text())] if path.exists() else []

    def route(self, sym: str):
        """The source holding `sym` and its name there."""
        if sym in self.crypto:
            return self.binance, sym
        if sym in YIELDS:
            return self.fred, YIELDS[sym][0]
        if sym in self.bist:
            return self.bist_src, sym.removesuffix(".IS") + ".IS"
        return self.yahoo, to_yahoo(sym)

    def sym(self, ticker: str) -> str:
        """The store key: an alias shares its index's bars (the core's lower-case share-class spelling undone)."""
        return base(sp.app_name(ticker).upper())

    # ── spreads ──
    async def spread_bars(self, name: str, tf: str, q, adjusted: bool) -> list:
        node = sp.parse(name)
        series = {}
        for leg in dict.fromkeys(sp.legs(node)):
            s = self.sym(leg)
            await self.bars.ensure(s, tf)
            tz = self.bars.tz(s)
            t0, t1 = ms_of(q["from"], tz=tz), ms_of(q["to"], True, tz=tz)
            rows = await self.bars.read(s, tf, t0, t1, adjusted)
            if tf == "1d":
                # Daily: matched by the trading date in each leg's own zone.
                series[leg] = {dt.datetime.fromtimestamp(r[0] / 1000, tz).date(): r for r in rows}
            else:
                series[leg] = {r[0]: r for r in rows}
        if tf == "1d":
            key_out = lambda d: int(dt.datetime.combine(d, dt.time(), NY).timestamp() * 1000)
        else:
            key_out = lambda t: t
        return sp.combine(node, series, key_out)

    async def spread_snap(self, t: str) -> dict | None:
        name = sp.app_name(t)
        node = sp.parse(name)
        names = list(dict.fromkeys(sp.legs(node)))
        got = await self.quotes(names)
        if any(n not in got for n in names):
            return None
        snaps = {n: self.snap(n, got[n]) for n in names}
        val = lambda bar, k: sp.evaluate(node, {n: float((snaps[n].get(bar) or {}).get(k) or 0.0) for n in names})
        try:
            last, prev = val("day", "c"), val("prev", "c")
            day = {k: val("day", k) for k in ("o", "h", "l", "c")}
        except ZeroDivisionError:
            return None
        day["h"], day["l"] = max(day.values()), min(day.values())
        upd = max(int(s.get("updated") or 0) for s in snaps.values())
        return {"ticker": t, "updated": upd, "change": last - prev, "changePct": (last / prev - 1) * 100 if prev else None,
                "day": {**day, "v": 0}, "prev": {"c": prev}, "minute": {"t": upd - upd % 60000, "o": last, "h": last, "l": last, "c": last, "v": 0}}

    def details(self, ticker: str) -> dict | None:
        t = ticker.upper()
        li = self.listings.get(t)
        m = self.store.meta(self.sym(t))
        if not li and not m:
            return None
        s = self.store.series(self.sym(t), "1d")
        list_date = str(dt.datetime.fromtimestamp(s.first_t / 1000, NY).date()) if s and s.first_t is not None else None
        name = (li.name if li else None) or (m or {}).get("name") or t
        typ = li.type if li else ("index" if is_index(t) else "stock")
        return {
            "ticker": t, "name": name, "type": typ, "market": li.market if li else ("indices" if typ == "index" else "stocks"),
            "exchange": li.exchange if li else "XNYS",
            "currency": (li.name.split(" / ")[-1] if li and li.type in ("crypto", "forex")
                         else (m or {}).get("currency") or ("TRY" if li and li.exchange == "BIST" else "%" if li and li.type == "bond" else "USD")),
            "listDate": list_date, "active": True, "locale": "us",
        }

    # ── quotes ──
    async def quotes(self, tickers: list[str]) -> dict[str, dict]:
        now = time.time()
        # Crypto is free and live: 5 s; Yahoo: 15 s.
        ttl = lambda t: 5.0 if self.sym(t) in self.crypto else QUOTE_TTL
        want = [t for t in tickers if now - self._quotes.get(t, (0, {}))[0] > ttl(t)]
        crypto = [t for t in want if self.sym(t) in self.crypto]
        if crypto:
            try:
                got = await self.binance.trading_day([self.sym(t) for t in crypto])
            except YahooError as e:
                print(f"[chartd] crypto quotes: {e}", flush=True)
                got = {}
            # The live candle: the current minute's real open / high / low / close / volume, not just the last price.
            mins = await asyncio.gather(*(self.binance.minute(self.sym(t)) for t in crypto), return_exceptions=True)
            for t, m in zip(crypto, mins):
                if self.sym(t) in got:
                    self._quotes[t] = (now, {"_binance": got[self.sym(t)], "_minute": None if isinstance(m, BaseException) else m})
        want = [t for t in want if t not in crypto]
        for t in [t for t in want if self.route(self.sym(t))[0] is self.fred]:
            # Daily values: the quote is the last stored day (after a fetch when there is none yet).
            try:
                await self.bars.ensure(self.sym(t), "1d")
                self._quotes[t] = (now, {"_stored": True})
            except YahooError as e:
                print(f"[chartd] {t}: {e}", flush=True)
        want = [t for t in want if self.route(self.sym(t))[0] in (self.yahoo, self.bist_src)]
        if want:
            ymap = {self.route(self.sym(t))[1]: t for t in want}
            try:
                got = await self.yahoo.quotes(list(ymap))
            except YahooError as e:
                print(f"[chartd] quotes: {e}", flush=True)
                got = {}
            for y, q in got.items():
                if y in ymap:
                    self._quotes[ymap[y]] = (now, q)
            # Aliases sharing one Yahoo symbol.
            for t in want:
                y = self.route(self.sym(t))[1]
                if y in got:
                    self._quotes[t] = (now, got[y])
        return {t: self._quotes[t][1] for t in tickers if t in self._quotes}

    def snap(self, t: str, q: dict) -> dict:
        if "_binance" in q:
            return self.crypto_snap(t, q["_binance"], q.get("_minute"))
        if "_stored" in q:
            return {**(self.stored_snap(self.sym(t)) or {}), "ticker": t}
        price = q.get("regularMarketPrice")
        rt = int((q.get("regularMarketTime") or 0) * 1000)
        prev_c = q.get("regularMarketPreviousClose")
        day = {"o": q.get("regularMarketOpen"), "h": q.get("regularMarketDayHigh"), "l": q.get("regularMarketDayLow"),
               "c": price, "v": q.get("regularMarketVolume") or 0}
        prev = {"c": prev_c}
        if rt:
            # The stored daily bar before the quote's session, when we have it (its range too).
            sym = self.sym(t)
            tz = self.bars.tz(sym)
            d0 = dt.datetime.combine(dt.datetime.fromtimestamp(rt / 1000, tz).date(), dt.time(), tz)
            before = self.store.bars(sym, "1d", int(d0.timestamp() * 1000) - 10 * 86400_000, int(d0.timestamp() * 1000) - 1)
            if before:
                p = before[-1]
                prev = {"o": p[1], "h": p[2], "l": p[3], "c": prev_c if prev_c is not None else p[4], "v": p[5]}
        minute = {"t": rt - rt % 60000, "o": price, "h": price, "l": price, "c": price, "v": 0}
        # Pre / post market: the latest trade outside the session.
        for k in ("postMarket", "preMarket"):
            pt, pp = q.get(f"{k}Time"), q.get(f"{k}Price")
            if pt and pp and pt * 1000 > rt:
                m = int(pt * 1000)
                minute = {"t": m - m % 60000, "o": pp, "h": pp, "l": pp, "c": pp, "v": 0}
                break
        return {"ticker": t, "updated": max(rt, minute["t"]), "change": q.get("regularMarketChange"),
                "changePct": q.get("regularMarketChangePercent"), "day": day, "prev": prev, "minute": minute}

    def crypto_snap(self, t: str, z: dict, kline: tuple | None = None) -> dict:
        """Binance's UTC trading day -> a snapshot (the day opened where the previous one closed)."""
        f = lambda k: float(z[k]) if z.get(k) is not None else None
        o, last = f("openPrice"), f("lastPrice")
        ct = int(z.get("closeTime") or time.time() * 1000)
        day0 = int(z.get("openTime") or 0)
        prev = {"c": o}
        before = self.store.bars(self.sym(t), "1d", day0 - 3 * 86400_000, day0 - 1) if day0 else []
        if before:
            p = before[-1]
            prev = {"o": p[1], "h": p[2], "l": p[3], "c": p[4], "v": p[5]}
        now_ms = int(time.time() * 1000)
        m = min(ct, now_ms)
        return {"ticker": t, "updated": m, "change": f("priceChange"), "changePct": f("priceChangePercent"),
                "day": {"o": o, "h": f("highPrice"), "l": f("lowPrice"), "c": last, "v": f("volume")}, "prev": prev,
                "minute": ({"t": kline[0], "o": kline[1], "h": kline[2], "l": kline[3], "c": kline[4], "v": kline[5]} if kline
                           else {"t": m - m % 60000, "o": last, "h": last, "l": last, "c": last, "v": 0})}

    def stored_snap(self, sym: str) -> dict | None:
        """A snapshot from the store alone (whole-market answers; no Yahoo call)."""
        r = self.store.last_bars(sym, "1d", 2)
        if len(r) < 2:
            return None
        a, b = r[1], r[0]
        w = lambda x: dict(zip("tohlcv", x))
        return {"ticker": sym, "updated": a[0] + 16 * 3600_000, "change": a[4] - b[4], "changePct": (a[4] / b[4] - 1) * 100 if b[4] else None,
                "day": w(a), "prev": w(b), "minute": {**w(a), "t": a[0] + int(15.98 * 3600_000)}}

    async def last_prices(self, tickers: list[str]) -> dict[str, float]:
        """Last price of each ticker (spreads included), for the closed-app alerts."""
        await self.load_listings()  # which source holds a ticker (crypto, BIST...) is known from the listings
        plain = [t for t in tickers if not sp.is_formula(t)]
        got = await self.quotes(plain)
        out = {}
        for t in plain:
            if t in got:
                c = (self.snap(t, got[t]).get("day") or {}).get("c")
                if c is not None:
                    out[t] = float(c)
        for t in tickers:
            if t not in plain:
                s = await self.spread_snap(t)
                if s and s["day"].get("c") is not None:
                    out[t] = float(s["day"]["c"])
        return out

    # ── closed-app alerts (the app's page calls these: same machine, another port) ──
    async def h_alert_rules(self, req: web.Request) -> web.Response:
        body = await req.json()
        n = self.alerts.set_rules(body.get("rules") or [])
        self.alerts.heartbeat()
        return web.json_response({"ok": True, "rules": n})

    async def h_heartbeat(self, req: web.Request) -> web.Response:
        self.alerts.heartbeat()
        return web.json_response({"ok": True})

    async def h_py(self, req: web.Request) -> web.Response:
        return web.json_response({"folder": str(__import__("chartd.pyind", fromlist=["folder"]).folder()), "indicators": self.py.listing()})

    async def h_fired(self, req: web.Request) -> web.Response:
        return web.json_response({"fired": self.alerts.fired_since(int(req.query.get("since", 0)))})

    # ── handlers ──
    async def h_bars(self, req: web.Request) -> web.Response:
        t = req.match_info["ticker"]  # not upper-cased: a lower-case letter is the core's spelling of a "/"
        q = req.query
        await self.load_listings()
        tf = store_tf(q.get("tf", "1d"))
        if tf is None:
            return web.json_response({"delayed": True, "truncated": False, **cols([])})
        if "from" not in q or "to" not in q:
            return err("BAD_REQUEST", "from/to", 400)
        pyi = PyIndicators.split(sp.app_name(t))
        if pyi:
            ind, under = pyi
            us = self.sym(under)
            try:
                await self.bars.ensure(us, tf)
                tz = self.bars.tz(us)
                t0, t1 = ms_of(q["from"], tz=tz), ms_of(q["to"], True, tz=tz)
                # Every stored bar up to the end: the indicator's warm-up is right whatever the range.
                rows = await self.bars.read(us, tf, -(2**62), t1, q.get("adjusted", "true") == "true")
                rows = [r for r in self.py.compute(ind, rows) if r[0] >= t0]
            except FileNotFoundError as e:
                return err("NOT_FOUND", str(e), 404)
            except NoData as e:
                return err("NOT_FOUND", f"{under}: {e}", 404)
            except YahooError as e:
                return err("UPSTREAM_ERROR", str(e), 502)
            except Exception as e:  # the indicator's own error
                return err("BAD_REQUEST", f"{ind}: {type(e).__name__}: {e}", 400)
            n = int(q.get("limit", 50000))
            cut = len(rows) > n
            rows = (rows[-n:][::-1] if q.get("order") == "desc" else rows[:n])
            return web.json_response({"delayed": True, "truncated": cut, "source": "python", "asOf": int(time.time() * 1000), **cols(rows)})
        if sp.is_formula(sp.app_name(t)):
            try:
                rows = await self.spread_bars(sp.app_name(t), tf, q, q.get("adjusted", "true") == "true")
            except (ValueError, sp.FormulaError) as e:
                return err("BAD_REQUEST", str(e), 400)
            except NoData as e:
                return err("NOT_FOUND", f"{t}: {e}", 404)
            except YahooError as e:
                return err("UPSTREAM_ERROR", f"{t}: {e}", 502)
            n = int(q.get("limit", 50000))
            cut = len(rows) > n
            rows = (rows[-n:][::-1] if q.get("order") == "desc" else rows[:n])
            return web.json_response({"delayed": True, "truncated": cut, "source": "spread", "asOf": int(time.time() * 1000), **cols(rows)})
        t = t.upper()
        try:
            await self.bars.ensure(self.sym(t), tf)
            # Dates are the exchange's days (a BIST day starts at Istanbul midnight, the evening before in New York).
            tz = self.bars.tz(self.sym(t))
            t0, t1 = ms_of(q["from"], tz=tz), ms_of(q["to"], True, tz=tz)
            rows = await self.bars.read(self.sym(t), tf, t0, t1, q.get("adjusted", "true") == "true")
        except ValueError:
            return err("BAD_REQUEST", "from/to", 400)
        except NoData as e:
            return err("NOT_FOUND", f"{t}: {e}", 404)
        except YahooError as e:
            return err("UPSTREAM_ERROR", f"{t}: {e}", 502)
        n = int(q.get("limit", 50000))
        cut = len(rows) > n
        desc = q.get("order") == "desc"
        rows = rows[-n:] if desc else rows[:n]
        if desc:
            rows = rows[::-1]
        s = self.store.series(self.sym(t), tf)
        src = self.route(self.sym(t))[0]
        source = {id(self.binance): "binance", id(self.fred): "fred", id(self.bist_src): "isyatirim+yahoo"}.get(id(src), "yahoo")
        return web.json_response({"delayed": True, "truncated": cut, "source": source,
                                  "asOf": int((s.fetched_at if s else time.time()) * 1000), **cols(rows)})

    async def h_snapshot_one(self, req: web.Request) -> web.Response:
        await self.load_listings()
        t = req.match_info["ticker"]
        if sp.is_formula(sp.app_name(t)):
            s = await self.spread_snap(t)
            return web.json_response(s) if s else err("NOT_FOUND", t, 404)
        t = t.upper()
        q = (await self.quotes([t])).get(t)
        if not q:
            return err("NOT_FOUND", t, 404)
        return web.json_response(self.snap(t, q))

    async def h_snapshot(self, req: web.Request) -> web.Response:
        tickers = [x.strip().upper() for x in req.query.get("tickers", "").split(",") if x.strip()]
        await self.load_listings()
        if tickers:
            plain = [t for t in tickers if not sp.is_formula(sp.app_name(t))]
            got = await self.quotes(plain)
            out = [self.snap(t, got[t]) for t in plain if t in got]
            for t in tickers:
                if t not in plain:
                    s = await self.spread_snap(t)
                    if s:
                        out.append(s)
            return web.json_response({"tickers": out})
        # The whole market (the screener polls it): live batch quotes cycling while it is asked for, else the last
        # two stored daily bars of every US ticker (rebuilt at most every five minutes).
        self.market.asked()
        if self.market.live_body and time.time() - self.market.live_at < 180:
            return web.Response(body=self.market.live_body, content_type="application/json")
        if not self._stored_market or time.time() - self._stored_market[0] > 300:
            snaps = [s for s in (self.stored_snap(x) for x in self.market.us_tickers()) if s]
            self._stored_market = (time.time(), json.dumps({"tickers": snaps}).encode())
        return web.Response(body=self._stored_market[1], content_type="application/json")

    async def h_tickers(self, req: web.Request) -> web.Response:
        """A prefix search, or (no prefix) the US stock market's reference list in pages of `limit`: `next` is the
        cursor of the following page (the screener's reference list)."""
        await self.load_listings()
        prefix = req.query.get("prefix", "").upper()
        typ = req.query.get("type")
        limit = int(req.query.get("limit", 1000))
        start = int(req.query.get("cursor", 0) or 0)
        if prefix:
            rows = [li for k, li in sorted(self.listings.items()) if k.startswith(prefix) and (not typ or li.type == typ)]
        else:
            rows = [li for _k, li in sorted(self.listings.items()) if li.market == "stocks" and li.exchange != "BIST"
                    and li.type in ("stock", "etf") and (not typ or li.type == typ)]
        page = rows[start : start + limit]
        nxt = str(start + limit) if not prefix and start + limit < len(rows) else None
        return web.json_response({"tickers": [{"ticker": li.ticker, "name": li.name, "type": li.type, "exchange": li.exchange,
                                               "currency": "USD", "market": li.market, "locale": "us", "active": True} for li in page],
                                  "next": nxt})

    async def h_screener_state(self, req: web.Request) -> web.Response:
        p = self.dir / "screener_state.json.gz"
        if not p.exists():
            return err("NOT_FOUND", "the screener state is built after the first nightly run", 404)
        return web.Response(body=p.read_bytes(), headers={"Content-Type": "application/json", "Content-Encoding": "gzip"})

    async def h_fill(self, req: web.Request) -> web.Response:
        """GET: how far the screener's market download is; POST: start it."""
        await self.load_listings()
        if req.method == "POST":
            self.market.start_fill()
        return web.json_response(self.market.fill_progress())

    async def h_screener_closes(self, req: web.Request) -> web.Response:
        p = self.dir / "screener_closes.bin.gz"
        if not p.exists():
            return err("NOT_FOUND", "the closes file is built after the first nightly run", 404)
        return web.Response(body=p.read_bytes(), content_type="application/octet-stream")

    async def delay(self, t: str) -> tuple[int | None, str]:
        """(minutes the prices lag, their source). Binance is live; Yahoo says per symbol (`exchangeDataDelayedBy`,
        and "Real Time" in `quoteSourceName` for Nasdaq's own feed)."""
        if self.sym(t) in self.crypto:
            return 0, "Binance"
        if self.sym(t) in YIELDS:
            return None, f"FRED {YIELDS[self.sym(t)][0]}, daily; each candle opens at the previous day's value"
        q = (await self.quotes([t])).get(t)
        if not q:
            return None, "Yahoo Finance"
        if "real time" in str(q.get("quoteSourceName") or "").lower():
            return 0, "Yahoo Finance (Nasdaq real time)"
        d = q.get("exchangeDataDelayedBy")
        return (int(d) if isinstance(d, (int, float)) else None), f"Yahoo Finance ({q.get('fullExchangeName') or q.get('exchange') or 'delayed'})"

    async def h_ticker(self, req: web.Request) -> web.Response:
        await self.load_listings()
        t = req.match_info["ticker"]
        name = sp.app_name(t)
        pyi = PyIndicators.split(name)
        if pyi:
            return web.json_response({"ticker": t, "name": f"{pyi[0]} of {pyi[1]}", "type": "indicator", "market": "stocks",
                                      "exchange": "PY", "currency": "", "listDate": None, "active": True, "locale": "us",
                                      "delayMinutes": None, "dataSource": f"Python indicator {pyi[0]} (chartd)"})
        if sp.is_formula(name):
            legs = list(dict.fromkeys(sp.legs(sp.parse(name))))
            delays = [await self.delay(x) for x in legs]
            ds = [d for d, _ in delays if d is not None]
            return web.json_response({"ticker": t, "name": name, "type": "spread", "market": "stocks", "exchange": "CALC",
                                      "currency": "", "listDate": None, "active": True, "locale": "us",
                                      "delayMinutes": max(ds) if ds else None,
                                      "dataSource": f"Spread of {', '.join(legs)}; OHLC computed field by field"})
        t = t.upper()
        d = self.details(t)
        if not d:
            return err("NOT_FOUND", t, 404)
        d["delayMinutes"], d["dataSource"] = await self.delay(t)
        return web.json_response(d)

    async def h_renames(self, req: web.Request) -> web.Response:
        return web.json_response({"renames": []})

    async def h_catalog(self, req: web.Request) -> web.Response:
        await self.load_listings()
        T = sorted(self.listings)
        L = [self.listings[t] for t in T]
        return web.json_response({"date": str(dt.datetime.now(NY).date()), "ticker": T, "name": [x.name for x in L],
                                  "type": [x.type for x in L], "exchange": [x.exchange for x in L], "market": [x.market for x in L]})

    async def _events(self, req: web.Request) -> tuple[str, str] | web.Response:
        await self.load_listings()
        t = req.query.get("ticker") or req.match_info.get("ticker") or ""
        if sp.is_formula(sp.app_name(t)) or PyIndicators.split(sp.app_name(t)):
            return err("NOT_FOUND", "a spread or an indicator has no corporate actions", 404)
        t = t.upper()
        if not t:
            return err("BAD_REQUEST", "ticker", 400)
        try:
            await self.bars.ensure(self.sym(t), "1d")
        except NoData:
            return err("NOT_FOUND", t, 404)
        except YahooError as e:
            return err("UPSTREAM_ERROR", str(e), 502)
        return t, self.sym(t)

    async def h_splits(self, req: web.Request) -> web.Response:
        r = await self._events(req)
        if isinstance(r, web.Response):
            return r
        _, sym = r
        tz = self.bars.tz(sym)
        sp = [{"date": str(dt.datetime.fromtimestamp(t, tz).date()), "from": den, "to": num} for t, num, den in self.store.splits(sym)]
        return web.json_response({"splits": sp[::-1]})

    async def h_dividends(self, req: web.Request) -> web.Response:
        r = await self._events(req)
        if isinstance(r, web.Response):
            return r
        _, sym = r
        tz = self.bars.tz(sym)
        splits = self.store.splits(sym)
        out = []
        for t, amount in self.store.dividends(sym):
            # Yahoo's amounts are split-adjusted; the app wants them as declared.
            k = 1.0
            for st, num, den in splits:
                if t < st:
                    k *= num / den
            out.append({"exDate": str(dt.datetime.fromtimestamp(t, tz).date()), "amount": amount * k})
        return web.json_response({"dividends": out[::-1]})

    async def h_daily(self, req: web.Request) -> web.Response:
        day = dt.date.fromisoformat(req.match_info["date"])
        t = int(dt.datetime.combine(day, dt.time(), NY).timestamp() * 1000)
        tk, c = [], []
        for sym in self.store.symbols_with("1d"):
            r = self.store.bars(sym, "1d", t, t)
            if r:
                tk.append(sym)
                c.append(r[0][4])
        return web.json_response({"tickers": tk, "c": c})

    async def h_archive(self, req: web.Request) -> web.Response:
        t = req.match_info["ticker"].upper()
        try:
            rows = await self.bars.read(self.sym(t), "1d", -(2**62), 2**62, adjusted=False)
        except NoData:
            return err("NOT_FOUND", t, 404)
        except YahooError as e:
            return err("UPSTREAM_ERROR", str(e), 502)
        return web.json_response({"version": 1, "ticker": t, "n": [0] * len(rows), **cols(rows)})

    async def h_econ_catalog(self, req: web.Request) -> web.Response:
        return web.json_response(econ_catalog())

    async def h_econ_series(self, req: web.Request) -> web.Response:
        sid = req.match_info["id"].upper()
        try:
            return web.json_response(await self.econ.series(sid))
        except NoData as e:
            return err("NOT_FOUND", str(e), 404)
        except YahooError as e:
            return err("UPSTREAM_ERROR", str(e), 502)

    async def h_health(self, req: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def h_install(self, req: web.Request) -> web.Response:
        await req.read()
        return web.json_response({"token": "oti1.local.chartd"})

    async def h_404(self, req: web.Request) -> web.Response:
        return err("NOT_FOUND", "not served by chartd", 404)


APP_ORIGINS = {"http://127.0.0.1:8799", "http://localhost:8799"}
ALERT_TASK = web.AppKey("alert_task", asyncio.Task)
NIGHTLY_TASK = web.AppKey("nightly_task", asyncio.Task)


@web.middleware
async def cors_mw(req: web.Request, handler):
    """The app's page (port 8799) may call /chartdesk/ routes; nothing else is opened."""
    origin = req.headers.get("Origin")
    allowed = req.path.startswith("/chartdesk/") and origin in APP_ORIGINS
    if req.method == "OPTIONS" and allowed:
        resp = web.Response(status=204)
    else:
        resp = await handler(req)
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@web.middleware
async def log_mw(req: web.Request, handler):
    t = time.time()
    resp = await handler(req)
    if req.path in ("/health", "/chartdesk/v1/heartbeat"):
        return resp
    print(f"{resp.status} {int((time.time() - t) * 1000):>5}ms {req.method} {req.path_qs[:150]}", flush=True)
    return resp


def make_app(store: Store, yahoo: Yahoo, dir: Path, binance: Binance | None = None) -> web.Application:
    a = App(store, yahoo, dir, binance)
    app = web.Application(middlewares=[log_mw, cors_mw])
    m = "/market/v1"
    app.add_routes([
        # A spread's formula may hold a "/" (before a digit or a bracket the core keeps it): the ticker takes the rest.
        web.get(f"{m}/bars/{{ticker:.+}}", a.h_bars),
        web.get(f"{m}/snapshot", a.h_snapshot),
        web.get(f"{m}/snapshot/{{ticker:.+}}", a.h_snapshot_one),
        web.get(f"{m}/tickers", a.h_tickers),
        web.get(f"{m}/tickers/{{ticker:.+}}/renames", a.h_renames),
        web.get(f"{m}/tickers/{{ticker:.+}}", a.h_ticker),
        web.get(f"{m}/catalog", a.h_catalog),
        web.get(f"{m}/splits", a.h_splits),
        web.get(f"{m}/splits/{{ticker}}", a.h_splits),
        web.get(f"{m}/dividends", a.h_dividends),
        web.get(f"{m}/dividends/{{ticker}}", a.h_dividends),
        web.get(f"{m}/daily/{{date}}", a.h_daily),
        web.get(f"{m}/economics/catalog", a.h_econ_catalog),
        web.get(f"{m}/economics/series/{{id}}", a.h_econ_series),
        web.get("/history/v1/daily/{ticker}", a.h_archive),
        web.get("/screener/v1/state", a.h_screener_state),
        web.get("/screener/v1/closes", a.h_screener_closes),
        web.post("/auth/v1/install", a.h_install),
        web.get("/health", a.h_health),
        web.post("/chartdesk/v1/alerts", a.h_alert_rules),
        web.post("/chartdesk/v1/heartbeat", a.h_heartbeat),
        web.get("/chartdesk/v1/alerts/fired", a.h_fired),
        web.get("/chartdesk/v1/py", a.h_py),
        web.route("*", "/chartdesk/v1/screener/fill", a.h_fill),
        web.get("/chartdesk/v1/agent", a.agent.h_window),
        web.route("*", "/mcp", a.agent.h_mcp),
        web.route("*", "/{tail:.*}", a.h_404),
    ])

    async def starting(_app):
        _app[ALERT_TASK] = asyncio.create_task(a.alerts.run())
        _app[NIGHTLY_TASK] = asyncio.create_task(a.market.scheduler())

    app.on_startup.append(starting)

    async def closing(_app):
        _app[ALERT_TASK].cancel()
        _app[NIGHTLY_TASK].cancel()
        await yahoo.close()
        await a.binance.close()
        await a.fred.close()
        await a.bist_src.close()
        await a.econ.close()

    app.on_cleanup.append(closing)
    return app


def main() -> None:
    p = argparse.ArgumentParser(description="Chart Desk data server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8798)
    a = p.parse_args()
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    print(f"[chartd] data in {d}, listening on {a.host}:{a.port}", flush=True)
    web.run_app(make_app(Store(d / "bars.sqlite"), Yahoo(), d), host=a.host, port=a.port, print=None)


if __name__ == "__main__":
    main()
