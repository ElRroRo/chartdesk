"""Binance spot market data (public, keyless: data-api.binance.vision, the market-data-only host).

Bars are klines (1000 a request, paged by start time); a day is a UTC day. Quotes are the UTC trading
day's open / high / low / last (`ticker/tradingDay`, 100 symbols a request).
"""
from __future__ import annotations

import asyncio
import json
import time

from curl_cffi.requests import AsyncSession

from .yahoo import Chart, NoData, YahooError

API = "https://data-api.binance.vision/api/v3"
#: Quote assets whose pairs are listed (the others are thin).
QUOTES = ("USDT", "USDC", "FDUSD", "TRY", "BTC", "ETH", "EUR")
META = {"exchangeTimezoneName": "UTC", "instrumentType": "CRYPTOCURRENCY", "fullExchangeName": "Binance"}


class BinanceError(YahooError):
    """Binance answered with an error (a YahooError so the server treats both sources alike)."""


class Binance:
    def __init__(self, concurrency: int = 4) -> None:
        self._s: AsyncSession | None = None
        self._sem = asyncio.Semaphore(concurrency)

    async def _session(self) -> AsyncSession:
        if self._s is None:
            self._s = AsyncSession(impersonate="chrome", timeout=30)
        return self._s

    async def close(self) -> None:
        if self._s is not None:
            await self._s.close()

    async def _get(self, path: str, params: dict):
        s = await self._session()
        last = None
        for attempt in range(4):
            async with self._sem:
                try:
                    r = await s.get(f"{API}/{path}", params=params)
                except Exception as e:
                    last = e
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
            if r.status_code in (418, 429) or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            if r.status_code == 400 and "Invalid symbol" in r.text:
                raise NoData(f"{params.get('symbol')}: not a Binance symbol")
            if r.status_code != 200:
                raise BinanceError(f"{path}: HTTP {r.status_code} {r.text[:120]}")
            return r.json()
        raise BinanceError(f"Binance did not answer ({last})")

    async def pairs(self) -> list[tuple[str, str, str]]:
        """(symbol, base, quote) of the spot pairs trading now, in the listed quote assets."""
        j = await self._get("exchangeInfo", {"permissions": "SPOT", "symbolStatus": "TRADING"})
        return [(z["symbol"], z["baseAsset"], z["quoteAsset"]) for z in j.get("symbols", []) if z.get("quoteAsset") in QUOTES]

    async def chart(self, symbol: str, interval: str, period1: int, period2: int | None = None) -> Chart:
        """Klines from `period1` (Unix s; before the listing means from the first bar) to now."""
        end_ms = int((period2 if period2 is not None else time.time()) * 1000)
        start = max(0, int(period1) * 1000)
        bars = []
        while start < end_ms:
            page = await self._get("klines", {"symbol": symbol, "interval": interval, "startTime": start, "endTime": end_ms, "limit": 1000})
            if not page:
                break
            for k in page:
                bars.append((int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])))
            if len(page) < 1000:
                break
            start = int(page[-1][0]) + 1
        if not bars and start == max(0, int(period1) * 1000):
            raise NoData(f"{symbol}: no bars")
        return Chart(bars, [], [], dict(META))

    async def minute(self, symbol: str) -> tuple | None:
        """The current 1-minute kline (Unix ms, o, h, l, c, v), or None."""
        k = await self._get("klines", {"symbol": symbol, "interval": "1m", "limit": 1})
        return (int(k[0][0]), *(float(x) for x in k[0][1:6])) if k else None

    async def trading_day(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i : i + 100]
            j = await self._get("ticker/tradingDay", {"symbols": json.dumps(chunk, separators=(",", ":")), "timeZone": "0"})
            for z in j if isinstance(j, list) else [j]:
                out[z["symbol"]] = z
        return out
