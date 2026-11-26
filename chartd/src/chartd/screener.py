"""The stock screener's nightly files, built from the bar store: the daily state and the closes.

The app's screener (crates/core/src/screener) joins a live whole-market snapshot with a state file holding, per US
ticker, values at the close of the last session D plus the carry-over each value needs to step into the next
session S (fields.rs). The contract was the upstream gateway's (private); this follows the app's own evaluation code:

- EMA<n>: Pine ta.ema at D, seeded with the SMA of its first n closes, over the last five years of closes.
- SMA<n> at D; SMA<n>|next = sum of the last n-1 closes.
- RSI / RSI7 (Wilder, seeded with the SMA of the first n changes) at D; RSI|gain, RSI|loss = the averages at D.
- ATR (Wilder, 14) at D; ATRP = 100 * ATR / close. ADR = SMA(high - low, 14); ADR|next = sum of the last 13 ranges.
- High/Low windows: at D over bars with date > D - N; |next over bars with date > S - N (up to D).
- Perf.<p>: at D, 100 * (close / base - 1) with base = the open of the last bar with date <= D - N; |next = that
  open for S. Perf.All: the first bar's open. Perf.YTD: the year's first bar's open.
- average_volume_<n>d_calc at D (D included); |next = sum of the last n-1 volumes. relative_volume_10d_calc at D =
  volume / the mean of the 10 before; |next = average_volume_10d_calc at D.
- volume_change, volume_change_abs at D against the session before; volume_change|next = D's volume.
- volume|1W / volume|1M at D (the week / month so far); |next = the same period's volume before S (0 in a new one).
- Volatility.W / .M: mean of 100 * (high - low) / low over the window; |s and |n (sum and count) for S.
- shares (market cap / last price, Nasdaq's screener file) and market_cap_basic; sector, industry (same file).
- dividends_yield_current: dividends with ex-date in the year to D / close; beta_1_year (weekly), beta_3_year and
  beta_5_year (monthly) against the S&P 500.
Technical ratings, oscillators and candlestick patterns are optional fields: absent, the app hides them.

Closes: gzip of a JSON header line {version, asof, sessions, tickers} and then each ticker's closes over the last
two years of sessions as little-endian float32, oldest first, NaN where the ticker has no bar.
"""
from __future__ import annotations

import bisect
import datetime as dt
import gzip
import json
import math
from pathlib import Path

import numpy as np

from .store import Store

NY_HOLIDAY_RULES = None  # see us_holidays()
FIVE_YEARS = 1260
CLOSES_SESSIONS = 504
EMAS = (5, 9, 10, 20, 21, 30, 50, 100, 200)
SMAS = (5, 10, 20, 30, 50, 100, 200)
WINDOWS = {"5D": ("days", 7), "1M": ("months", 1), "3M": ("months", 3), "6M": ("months", 6)}
PERFS = {"W": ("days", 7), "5D": ("days", 7), "1M": ("months", 1), "3M": ("months", 3), "6M": ("months", 6),
         "Y": ("years", 1), "5Y": ("years", 5), "10Y": ("years", 10)}


# ── calendar ──

def _nth_weekday(y: int, m: int, wd: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, wd: int) -> dt.date:
    d = dt.date(y, m + 1, 1) - dt.timedelta(days=1) if m < 12 else dt.date(y, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def _easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(y, month, day)


def us_holidays(y: int) -> set[dt.date]:
    """NYSE full closes of year y (a Saturday holiday is observed on Friday, a Sunday one on Monday)."""
    def obs(d: dt.date) -> dt.date:
        return d - dt.timedelta(days=1) if d.weekday() == 5 else d + dt.timedelta(days=1) if d.weekday() == 6 else d
    out = {
        obs(dt.date(y, 1, 1)), _nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3), _easter(y) - dt.timedelta(days=2),
        _last_weekday(y, 5, 0), obs(dt.date(y, 7, 4)), _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4),
        obs(dt.date(y, 12, 25)),
    }
    if y >= 2022:
        out.add(obs(dt.date(y, 6, 19)))
    return out


def next_session(d: dt.date) -> dt.date:
    s = d + dt.timedelta(days=1)
    while s.weekday() >= 5 or s in us_holidays(s.year):
        s += dt.timedelta(days=1)
    return s


def back(d: dt.date, unit: str, n: int) -> dt.date:
    if unit == "days":
        return d - dt.timedelta(days=n)
    if unit == "months":
        m = d.month - 1 - n
        y = d.year + m // 12
        m = m % 12 + 1
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
        return dt.date(y, m, min(d.day, last))
    try:
        return d.replace(year=d.year - n)
    except ValueError:  # 29 February
        return d.replace(year=d.year - n, day=28)


# ── indicators (Pine semantics) ──

def ema(c, n: int) -> float | None:
    """Pine ta.ema at the last value, seeded with the SMA of the first n (a plain loop over floats: fast enough)."""
    c = c.tolist() if isinstance(c, np.ndarray) else c
    if len(c) < n:
        return None
    e = sum(c[:n]) / n
    k = 2.0 / (n + 1)
    for x in c[n:]:
        e += k * (x - e)
    return e


def wilder(x, n: int) -> float | None:
    """RMA seeded with the SMA of the first n values."""
    x = x.tolist() if isinstance(x, np.ndarray) else x
    if len(x) < n:
        return None
    a = sum(x[:n]) / n
    for v in x[n:]:
        a = (a * (n - 1) + v) / n
    return a


def rsi_parts(c: np.ndarray, n: int) -> tuple[float, float, float] | None:
    ch = np.diff(c)
    if len(ch) < n:
        return None
    g, lo = wilder(np.maximum(ch, 0), n), wilder(np.maximum(-ch, 0), n)
    rsi = 100.0 if lo == 0 else 100.0 - 100.0 / (1.0 + g / lo)
    return rsi, g, lo


def atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int) -> float | None:
    if len(c) < n:
        return None
    pc = np.concatenate([[np.nan], c[:-1]])
    tr = np.where(np.isnan(pc), h - lo, np.maximum.reduce([h - lo, np.abs(h - pc), np.abs(lo - pc)]))
    return wilder(tr, n)


def beta(stock: tuple[list[dt.date], list[float]], index: tuple[list[dt.date], list[float]], dates: list[dt.date]) -> float | None:
    """Beta of period returns sampled at `dates` (the last close on or before each). Series as sorted (dates, values)."""
    def at(series: tuple[list[dt.date], list[float]], d: dt.date) -> float | None:
        i = bisect.bisect_right(series[0], d) - 1
        return series[1][i] if i >= 0 else None
    s = [at(stock, d) for d in dates]
    x = [at(index, d) for d in dates]
    rs, rx = [], []
    for i in range(1, len(dates)):
        if None in (s[i - 1], s[i], x[i - 1], x[i]) or not s[i - 1] or not x[i - 1]:
            continue
        rs.append(s[i] / s[i - 1] - 1)
        rx.append(x[i] / x[i - 1] - 1)
    if len(rs) < 0.8 * (len(dates) - 1) or len(rs) < 10:
        return None
    var = float(np.var(rx))
    return float(np.cov(rs, rx, bias=True)[0][1] / var) if var else None


# ── the state ──

def ticker_state(d: list[dt.date], o, h, lo, c, v, D: dt.date, S: dt.date, divs: list[tuple[dt.date, float]],
                 index: tuple[list[dt.date], list[float]] | None, shares: float | None) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    n = len(c)
    cD, vD = float(c[-1]), float(v[-1])
    out["close"] = cD
    c5 = c[-FIVE_YEARS:]
    for k in EMAS:
        out[f"EMA{k}"] = ema(c5, k)
    for k in SMAS:
        out[f"SMA{k}"] = float(c[-k:].mean()) if n >= k else None
        out[f"SMA{k}|next"] = float(c[-(k - 1):].sum()) if n >= k - 1 and k > 1 else None
    for key, k in (("RSI", 14), ("RSI7", 7)):
        p = rsi_parts(c5, k)
        out[key], out[f"{key}|gain"], out[f"{key}|loss"] = p if p else (None, None, None)
    a = atr(h[-FIVE_YEARS:], lo[-FIVE_YEARS:], c5, 14)
    out["ATR"] = a
    out["ATRP"] = 100 * a / cD if a and cD else None
    rng = h - lo
    out["ADR"] = float(rng[-14:].mean()) if n >= 14 else None
    out["ADR|next"] = float(rng[-13:].sum()) if n >= 13 else None
    out["ADRP"] = 100 * out["ADR"] / cD if out["ADR"] and cD else None
    da = np.array(d, dtype="datetime64[D]")

    def since(start: dt.date) -> np.ndarray:
        return da > np.datetime64(start)

    for name, (unit, k) in {**WINDOWS, "52W": ("days", 364)}.items():
        hi_key, lo_key = (f"High.{name}", f"Low.{name}") if name != "52W" else ("price_52_week_high", "price_52_week_low")
        m = since(back(D, unit, k))
        out[hi_key], out[lo_key] = (float(h[m].max()), float(lo[m].min())) if m.any() else (None, None)
        m2 = since(back(S, unit, k))
        out[f"{hi_key}|next"], out[f"{lo_key}|next"] = (float(h[m2].max()), float(lo[m2].min())) if m2.any() else (None, None)

    def base_on(day: dt.date) -> float | None:
        i = np.searchsorted(da, np.datetime64(day), side="right") - 1
        return float(o[i]) if i >= 0 else None

    for name, (unit, k) in PERFS.items():
        b, bn = base_on(back(D, unit, k)), base_on(back(S, unit, k))
        out[f"Perf.{name}"] = 100 * (cD / b - 1) if b else None
        out[f"Perf.{name}|next"] = bn
    out["Perf.All"] = 100 * (cD / float(o[0]) - 1) if o[0] else None
    out["Perf.All|next"] = float(o[0])
    first_of_year = np.searchsorted(da, np.datetime64(dt.date(D.year, 1, 1)))
    ytd = float(o[first_of_year]) if first_of_year < n else None
    out["Perf.YTD"] = 100 * (cD / ytd - 1) if ytd else None
    out["Perf.YTD|next"] = ytd
    for k in (10, 30, 60, 90):
        key = f"average_volume_{k}d_calc"
        out[key] = float(v[-k:].mean()) if n >= k else None
        out[f"{key}|next"] = float(v[-(k - 1):].sum()) if n >= k - 1 else None
    prev10 = float(v[-11:-1].mean()) if n >= 11 else None
    out["relative_volume_10d_calc"] = vD / prev10 if prev10 else None
    out["relative_volume_10d_calc|next"] = out["average_volume_10d_calc"]
    vP = float(v[-2]) if n >= 2 else None
    out["volume_change"] = 100 * (vD / vP - 1) if vP else None
    out["volume_change_abs"] = vD - vP if vP is not None else None
    out["volume_change|next"] = vD
    # Period volumes: the week (ISO) / month of D so far, and what S's period already holds.
    wk = lambda x: x.isocalendar()[:2]
    for key, same in (("volume|1W", lambda x, y: wk(x) == wk(y)), ("volume|1M", lambda x, y: (x.year, x.month) == (y.year, y.month))):
        cur = sum(float(v[i]) for i in range(max(0, n - 25), n) if same(d[i], D))
        out[key] = cur
        out[f"{key}|next"] = cur if same(S, D) else 0.0
    for key, days in (("Volatility.W", 7), ("Volatility.M", 30)):
        sw = 100 * (h - lo) / np.where(lo > 0, lo, np.nan)
        m = since(D - dt.timedelta(days=days))
        out[key] = float(np.nanmean(sw[m])) if m.any() else None
        m2 = since(S - dt.timedelta(days=days))
        out[f"{key}|s"], out[f"{key}|n"] = (float(np.nansum(sw[m2])), float(m2.sum())) if m2.any() else (0.0, 0.0)
    if shares:
        out["shares"] = shares
        out["market_cap_basic"] = cD * shares
    year_ago = D - dt.timedelta(days=365)
    paid = sum(a for when, a in divs if year_ago < when <= D)
    out["dividends_yield_current"] = 100 * paid / cD if cD else None
    if index:
        closes = (list(d), c.tolist())
        weekly = [D - dt.timedelta(weeks=i) for i in range(52, -1, -1)]
        monthly = lambda yrs: [back(D, "months", i) for i in range(12 * yrs, -1, -1)]
        out["beta_1_year"] = beta(closes, index, weekly)
        out["beta_3_year"] = beta(closes, index, monthly(3))
        out["beta_5_year"] = beta(closes, index, monthly(5))
    return {k: (None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x) for k, x in out.items()}


def build(store: Store, tickers: list[str], tz_of, fundamentals: dict[str, dict], out_dir: Path) -> dict:
    """Write screener_state.json.gz and screener_closes.bin.gz for `tickers`; returns a summary.

    One ticker at a time (ten years of its bars, then dropped): only each ticker's two-year closes row is kept. The
    sessions are SPY's (it trades every session)."""
    tz = tz_of("SPY")
    day = lambda ms: dt.datetime.fromtimestamp(ms / 1000, tz).date()
    spy = store.last_bars("SPY", "1d", CLOSES_SESSIONS + 5)
    if not spy:
        raise RuntimeError("no SPY daily bars in the store (the market's session calendar)")
    D = day(spy[-1][0])
    S = next_session(D)
    sessions = [day(r[0]) for r in spy if day(r[0]) <= D][-CLOSES_SESSIONS:]
    pos = {x: j for j, x in enumerate(sessions)}
    spx = store.last_bars("SPX", "1d", 2600)
    index = ([day(r[0]) for r in spx], [r[4] for r in spx]) if spx else None
    names: list[str] = []
    cols: dict[str, list] = {}
    text: dict[str, list] = {"sector": [], "industry": []}
    body = bytearray()
    for t in sorted(tickers):
        rows = store.last_bars(t, "1d", 2600)  # ten years of sessions and a little more
        if len(rows) < 2:
            continue
        a = np.array(rows, dtype=float)
        dates = [day(x) for x in a[:, 0]]
        keep = [i for i, x in enumerate(dates) if x <= D]
        if len(keep) < 2:
            continue
        a = a[keep]
        dates = [dates[i] for i in keep]
        f = fundamentals.get(t, {})
        divs = [(dt.datetime.fromtimestamp(ts, tz).date(), amt) for ts, amt in store.dividends(t)]
        st = ticker_state(dates, a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], D, S, divs, index, f.get("shares"))
        i = len(names)
        names.append(t)
        for k, x in st.items():
            cols.setdefault(k, [None] * i).append(x)
        for k in cols:
            if len(cols[k]) < i + 1:
                cols[k].append(None)
        text["sector"].append(f.get("sector") or None)
        text["industry"].append(f.get("industry") or None)
        row = np.full(len(sessions), np.nan, dtype="<f4")
        for d_, c_ in zip(dates[-CLOSES_SESSIONS - 5:], a[-CLOSES_SESSIONS - 5:, 4]):
            j = pos.get(d_)
            if j is not None:
                row[j] = c_
        body += row.tobytes()
    state = {"version": 1, "asof": D.isoformat(), "tickers": names, "num": cols, "text": text}
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "screener_state.json.gz.tmp"
    tmp.write_bytes(gzip.compress(json.dumps(state, separators=(",", ":")).encode(), 6))
    tmp.replace(out_dir / "screener_state.json.gz")
    header = json.dumps({"version": 1, "asof": D.isoformat(), "sessions": [x.isoformat() for x in sessions], "tickers": names})
    tmp = out_dir / "screener_closes.bin.gz.tmp"
    tmp.write_bytes(gzip.compress(header.encode() + b"\n" + bytes(body), 6))
    tmp.replace(out_dir / "screener_closes.bin.gz")
    return {"asof": D.isoformat(), "next": S.isoformat(), "tickers": len(names), "keys": len(cols)}
