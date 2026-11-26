"""Economic series behind the app's ECONOMICS:<ID> symbols: a curated FRED list (keyless) and, with a key,
the Turkish central bank's EVDS (CHARTD_TCMB_KEY; a free key from evds3.tcmb.gov.tr).

The catalog lists each series with its frequency, units ("Billions of Dollars": the app scales to full units by
the first word), seasonal adjustment, source, category and country. A series answer is its observation dates
(Unix ms, 00:00 UTC) and values.

EVDS: the working base is /igmevdsms-dis/ (the documented /service/evds/ answers 200 with an HTML shell), and the
key is an HTTP header named `key` (Kaiju's adapter found both).
"""
from __future__ import annotations

import datetime as dt
import os
import time

from curl_cffi.requests import AsyncSession

from .fred import Fred
from .yahoo import NoData, YahooError

# (id, name, freq, units, units_short, seasonal, category)
FRED_SERIES: list[tuple[str, str, str, str, str, str, str]] = [
    ("IORB", "Interest Rate on Reserve Balances", "D", "Percent", "%", "NSA", "Interest rates"),
    ("DFF", "Federal Funds Effective Rate", "D", "Percent", "%", "NSA", "Interest rates"),
    ("FEDFUNDS", "Federal Funds Effective Rate (monthly)", "M", "Percent", "%", "NSA", "Interest rates"),
    ("DFEDTARU", "Federal Funds Target Range, Upper Limit", "D", "Percent", "%", "NSA", "Interest rates"),
    ("DFEDTARL", "Federal Funds Target Range, Lower Limit", "D", "Percent", "%", "NSA", "Interest rates"),
    ("SOFR", "Secured Overnight Financing Rate", "D", "Percent", "%", "NSA", "Interest rates"),
    ("T10Y2Y", "10-Year minus 2-Year Treasury Yield Spread", "D", "Percent", "%", "NSA", "Interest rates"),
    ("T10Y3M", "10-Year minus 3-Month Treasury Yield Spread", "D", "Percent", "%", "NSA", "Interest rates"),
    ("DFII10", "10-Year TIPS Yield (real)", "D", "Percent", "%", "NSA", "Interest rates"),
    ("T10YIE", "10-Year Breakeven Inflation Rate", "D", "Percent", "%", "NSA", "Prices"),
    ("MORTGAGE30US", "30-Year Fixed Mortgage Average", "W", "Percent", "%", "NSA", "Housing"),
    ("BAMLH0A0HYM2", "US High Yield Option-Adjusted Spread", "D", "Percent", "%", "NSA", "Credit"),
    ("BAMLC0A0CM", "US Corporate (Investment Grade) Option-Adjusted Spread", "D", "Percent", "%", "NSA", "Credit"),
    ("NFCI", "Chicago Fed National Financial Conditions Index", "W", "Index", "Index", "NSA", "Credit"),
    ("CPIAUCSL", "Consumer Price Index, All Items", "M", "Index 1982-1984=100", "Index", "SA", "Prices"),
    ("CPILFESL", "Consumer Price Index, Core (less food and energy)", "M", "Index 1982-1984=100", "Index", "SA", "Prices"),
    ("PCEPI", "PCE Price Index", "M", "Index 2017=100", "Index", "SA", "Prices"),
    ("PCEPILFE", "Core PCE Price Index", "M", "Index 2017=100", "Index", "SA", "Prices"),
    ("PPIACO", "Producer Price Index, All Commodities", "M", "Index 1982=100", "Index", "NSA", "Prices"),
    ("UNRATE", "Unemployment Rate", "M", "Percent", "%", "SA", "Labor"),
    ("PAYEMS", "Nonfarm Payrolls", "M", "Thousands of Persons", "Persons", "SA", "Labor"),
    ("ICSA", "Initial Jobless Claims", "W", "Number", "Number", "SA", "Labor"),
    ("CIVPART", "Labor Force Participation Rate", "M", "Percent", "%", "SA", "Labor"),
    ("JTSJOL", "Job Openings (JOLTS)", "M", "Thousands", "Number", "SA", "Labor"),
    ("CES0500000003", "Average Hourly Earnings, Private", "M", "Dollars per Hour", "USD", "SA", "Labor"),
    ("GDP", "Gross Domestic Product", "Q", "Billions of Dollars", "USD", "SAAR", "GDP"),
    ("GDPC1", "Real Gross Domestic Product", "Q", "Billions of Chained 2017 Dollars", "USD", "SAAR", "GDP"),
    ("A191RL1Q225SBEA", "Real GDP Growth (annualized)", "Q", "Percent", "%", "SAAR", "GDP"),
    ("INDPRO", "Industrial Production Index", "M", "Index 2017=100", "Index", "SA", "Business"),
    ("RSAFS", "Retail Sales", "M", "Millions of Dollars", "USD", "SA", "Consumer"),
    ("UMCSENT", "University of Michigan Consumer Sentiment", "M", "Index 1966:Q1=100", "Index", "NSA", "Consumer"),
    ("M2SL", "M2 Money Stock", "M", "Billions of Dollars", "USD", "SA", "Money"),
    ("WALCL", "Federal Reserve Total Assets", "W", "Millions of Dollars", "USD", "NSA", "Money"),
    ("WRESBAL", "Reserve Balances with Federal Reserve Banks", "W", "Billions of Dollars", "USD", "NSA", "Money"),
    ("RRPONTSYD", "Overnight Reverse Repurchase Agreements", "D", "Billions of Dollars", "USD", "NSA", "Money"),
    ("WTREGEN", "Treasury General Account", "W", "Millions of Dollars", "USD", "NSA", "Money"),
    ("HOUST", "Housing Starts", "M", "Thousands of Units", "Units", "SAAR", "Housing"),
    ("CSUSHPINSA", "S&P CoreLogic Case-Shiller US Home Price Index", "M", "Index Jan 2000=100", "Index", "NSA", "Housing"),
    ("DTWEXBGS", "Nominal Broad US Dollar Index", "D", "Index Jan 2006=100", "Index", "NSA", "Trade"),
    ("DCOILWTICO", "Crude Oil Prices: WTI", "D", "Dollars per Barrel", "USD", "NSA", "Commodities"),
]
# TradingView's ECONOMICS codes for Turkey -> EVDS series. TRGRES is TP.AB.A01 (Kaiju verified it live
# 2026-08-17). TRFER (foreign exchange reserves) waits until its EVDS code is read off EVDS's own catalogue.
TCMB_SERIES: list[tuple[str, str, str, str, str, str, str, str]] = [
    ("TRGRES", "TP.AB.A01", "Turkey Gross International Reserves", "W", "Millions of Dollars", "USD", "NSA", "Money"),
]
COUNTRIES = [
    {"code": "US", "name": "United States", "region": "North America"},
    {"code": "TR", "name": "Turkey", "region": "Europe"},
]
EVDS = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"


def tcmb_key() -> str | None:
    return os.environ.get("CHARTD_TCMB_KEY") or None


def catalog() -> dict:
    rows = [(i, n, f, u, us, s, "Federal Reserve Bank of St. Louis (FRED)", c, "US") for i, n, f, u, us, s, c in FRED_SERIES]
    if tcmb_key():
        rows += [(i, n, f, u, us, s, "Central Bank of the Republic of Turkiye (EVDS)", c, "TR") for i, _code, n, f, u, us, s, c in TCMB_SERIES]
    cols = ("id", "name", "freq", "units", "unitsShort", "seasonal", "source", "category", "country")
    out = {k: [r[j] for r in rows] for j, k in enumerate(cols)}
    return {"date": str(dt.date.today()), **out, "countries": COUNTRIES}


def meta_of(series_id: str) -> dict | None:
    for i, n, f, u, us, s, c in FRED_SERIES:
        if i == series_id:
            return {"id": i, "name": n, "freq": f, "units": u, "unitsShort": us, "seasonal": s,
                    "source": "Federal Reserve Bank of St. Louis (FRED)", "category": c, "country": "US"}
    for i, _code, n, f, u, us, s, c in TCMB_SERIES:
        if i == series_id and tcmb_key():
            return {"id": i, "name": n, "freq": f, "units": u, "unitsShort": us, "seasonal": s,
                    "source": "Central Bank of the Republic of Turkiye (EVDS)", "category": c, "country": "TR"}
    return None


def utc_ms(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


class Econ:
    def __init__(self, fred: Fred) -> None:
        self.fred = fred
        self._s: AsyncSession | None = None
        self._cache: dict[str, tuple[float, list[tuple[dt.date, float]]]] = {}

    async def close(self) -> None:
        if self._s is not None:
            await self._s.close()

    async def series(self, series_id: str) -> dict:
        m = meta_of(series_id)
        if m is None:
            raise NoData(f"no economic series {series_id}")
        if m["country"] == "TR":
            rows = await self._evds(next(code for i, code, *_ in TCMB_SERIES if i == series_id))
        else:
            rows = await self.fred.observations(series_id)
        return {**m, "t": [utc_ms(d) for d, _ in rows], "v": [v for _, v in rows]}

    async def _evds(self, code: str) -> list[tuple[dt.date, float]]:
        hit = self._cache.get(code)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        key = tcmb_key()
        if not key:
            raise NoData("EVDS needs CHARTD_TCMB_KEY")
        if self._s is None:
            self._s = AsyncSession(impersonate="chrome", timeout=60)
        end = dt.date.today().strftime("%d-%m-%Y")
        r = await self._s.get(f"{EVDS}series={code}&startDate=01-01-1990&endDate={end}&type=json", headers={"key": key})
        if r.status_code != 200 or r.text.lstrip().startswith("<"):
            raise YahooError(f"EVDS {code}: HTTP {r.status_code}")
        field = code.replace(".", "_")
        rows = []
        for it in r.json().get("items") or []:
            v = it.get(field)
            d = _evds_date(it.get("Tarih"))
            if v in (None, "") or d is None:
                continue
            try:
                rows.append((d, float(str(v).replace(",", ""))))
            except ValueError:
                continue
        rows.sort()
        self._cache[code] = (time.time(), rows)
        return rows


def _evds_date(text) -> dt.date | None:
    """EVDS writes 01-01-2026 (daily, weekly) and 2026-1 (monthly)."""
    if not text:
        return None
    t = str(text).strip()
    try:
        parts = t.split("-")
        if len(parts) == 3 and len(parts[0]) == 2:
            return dt.date(int(parts[2]), int(parts[1]), int(parts[0]))
        if len(parts) == 2:
            return dt.date(int(parts[0]), int(parts[1]), 1)
        if len(parts) == 3:
            return dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None
    return None
