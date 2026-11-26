"""Yahoo Finance chart and quote API (unofficial; each install fetches for its own user).

Rules (plan, each one a test):
- never `range=max` (it silently returns quarterly bars): always `period1`/`period2`;
- the answer's `dataGranularity` must be the interval asked for;
- bars with a null field are dropped;
- OHLC come split-adjusted (Yahoo's own); splits and dividends come in the same answer.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"
CRUMB = "https://query1.finance.yahoo.com/v1/test/getcrumb"
COOKIE = "https://fc.yahoo.com"
#: 1900-01-01: "from the first bar" for daily history.
EPOCH_1900 = -2208988800
#: Days of history Yahoo serves per intraday interval (measured 2026-11-26: 1m answered 8 days and refused 29).
INTRADAY_DAYS = {"1m": 7, "5m": 59, "15m": 59, "1h": 729}

Bar = tuple[int, float, float, float, float, float]  # (epoch s, o, h, l, c, v)


class YahooError(Exception):
    """Yahoo answered with an error, or not at all."""


class NoData(YahooError):
    """Yahoo knows no such symbol, or has no bars for it."""


@dataclass
class Chart:
    bars: list[Bar]
    #: (epoch s, numerator, denominator): a 10-for-1 split is (t, 10, 1).
    splits: list[tuple[int, float, float]] = field(default_factory=list)
    #: (epoch s, amount per share, split-adjusted as Yahoo sends it).
    dividends: list[tuple[int, float]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def parse_chart(j: dict, interval: str) -> Chart:
    """One `v8/finance/chart` answer -> bars, events and meta (raises on errors and wrong granularity)."""
    c = j.get("chart") or {}
    err = c.get("error")
    if err:
        desc = (err or {}).get("description") or (err or {}).get("code") or "error"
        if (err or {}).get("code") == "Not Found" or "No data found" in desc:
            raise NoData(desc)
        raise YahooError(desc)
    res = (c.get("result") or [None])[0]
    if not res:
        raise NoData("empty answer")
    meta = res.get("meta") or {}
    got = meta.get("dataGranularity")
    if got != interval:
        raise YahooError(f"asked {interval} bars, Yahoo answered {got}")
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    cols = [q.get(k) or [None] * len(ts) for k in ("open", "high", "low", "close", "volume")]
    bars: list[Bar] = [
        (int(t), float(o), float(h), float(lo), float(cl), float(v or 0))
        for t, o, h, lo, cl, v in zip(ts, *cols)
        if None not in (o, h, lo, cl)
    ]
    ev = res.get("events") or {}
    splits = sorted((int(s["date"]), float(s["numerator"]), float(s["denominator"])) for s in (ev.get("splits") or {}).values())
    divs = sorted((int(d["date"]), float(d["amount"])) for d in (ev.get("dividends") or {}).values())
    return Chart(bars, splits, divs, meta)


class Yahoo:
    """One shared HTTP session (Chrome TLS fingerprint), a few requests at a time, backing off when throttled."""

    def __init__(self, concurrency: int = 4) -> None:
        self._s: AsyncSession | None = None
        self._sem = asyncio.Semaphore(concurrency)
        self._crumb: str | None = None
        self._crumb_lock = asyncio.Lock()

    async def _session(self) -> AsyncSession:
        if self._s is None:
            self._s = AsyncSession(impersonate="chrome", timeout=40)
        return self._s

    async def close(self) -> None:
        if self._s is not None:
            await self._s.close()

    async def _get(self, url: str, params: dict):
        s = await self._session()
        last = None
        for attempt in range(4):
            async with self._sem:
                try:
                    r = await s.get(url, params=params)
                except Exception as e:  # network, timeout
                    last = e
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            return r
        raise YahooError(f"Yahoo did not answer ({last})")

    async def chart(self, ysym: str, interval: str, period1: int, period2: int | None = None) -> Chart:
        params = {
            "interval": interval,
            "period1": int(period1),
            "period2": int(period2 if period2 is not None else time.time()),
            "events": "div,splits",
            "includePrePost": "false",
        }
        r = await self._get(CHART.format(quote(ysym, safe="")), params)
        try:
            j = r.json()
        except Exception as e:
            raise YahooError(f"{ysym}: not JSON (HTTP {r.status_code})") from e
        return parse_chart(j, interval)

    async def _ensure_crumb(self) -> str:
        async with self._crumb_lock:
            if self._crumb:
                return self._crumb
            s = await self._session()
            await s.get(COOKIE)  # sets Yahoo's cookie; a 404 page is normal
            r = await s.get(CRUMB)
            text = (r.text or "").strip()
            if r.status_code != 200 or not text or "<" in text:
                raise YahooError(f"no quote crumb (HTTP {r.status_code})")
            self._crumb = text
            return text

    async def quotes(self, ysyms: list[str]) -> dict[str, dict]:
        """Latest quotes, 50 symbols a request. Keyed by the Yahoo symbol."""
        out: dict[str, dict] = {}
        for i in range(0, len(ysyms), 50):
            chunk = ysyms[i : i + 50]
            for retry in range(2):
                crumb = await self._ensure_crumb()
                r = await self._get(QUOTE, {"symbols": ",".join(chunk), "crumb": crumb})
                if r.status_code in (401, 403) and retry == 0:
                    self._crumb = None
                    continue
                if r.status_code != 200:
                    raise YahooError(f"quotes: HTTP {r.status_code}")
                for z in (r.json().get("quoteResponse") or {}).get("result") or []:
                    out[z["symbol"]] = z
                break
        return out
