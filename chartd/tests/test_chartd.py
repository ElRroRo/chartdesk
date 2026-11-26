"""chartd tests on recorded Yahoo answers (tests/fixtures, fetched 2026-11-26) and a fake Yahoo; no network."""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from chartd.bars import Bars, rows_of, unadjusted
from chartd.bist import actions, daily_rows
from chartd.server import make_app
from chartd.store import Store
from chartd.symbols import clean_name, parse_nasdaqlisted, parse_otherlisted, to_yahoo, tr_title
from chartd.yahoo import Chart, NoData, Yahoo, YahooError, parse_chart

F = Path(__file__).parent / "fixtures"
NY = ZoneInfo("America/New_York")


def fx(name: str) -> dict:
    return json.loads((F / name).read_text())


def ny_ms(d: str) -> int:
    return int(dt.datetime.combine(dt.date.fromisoformat(d), dt.time(), NY).timestamp() * 1000)


# ── Yahoo answers ──

def test_parse_daily_with_split():
    c = parse_chart(fx("aapl_1d_2020split.json"), "1d")
    assert c.splits and c.splits[0][1:] == (4.0, 1.0)  # AAPL 4-for-1, 2020-08-31
    assert all(None not in b for b in c.bars)


def test_granularity_must_match():
    # Asking 1d and getting weekly bars (what range=max does) is an error, never data.
    with pytest.raises(YahooError):
        parse_chart(fx("aapl_1wk.json"), "1d")


def test_unknown_symbol_is_nodata():
    with pytest.raises(NoData):
        parse_chart(fx("notfound.json"), "1d")


def test_null_bars_dropped():
    j = copy.deepcopy(fx("aapl_1d_2020split.json"))
    q = j["chart"]["result"][0]["indicators"]["quote"][0]
    q["close"][3] = None
    n = len(j["chart"]["result"][0]["timestamp"])
    assert len(parse_chart(j, "1d").bars) == n - 1


async def test_never_range_max(monkeypatch):
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return fx("aapl_1d_2020split.json")

    async def fake_get(url, params):
        seen.update(params)
        return R()

    y = Yahoo()
    monkeypatch.setattr(y, "_get", fake_get)
    await y.chart("AAPL", "1d", 0)
    assert "range" not in seen and "period1" in seen and "period2" in seen


# ── bars ──

def test_unadjusted_undoes_the_split():
    c = parse_chart(fx("aapl_1d_2020split.json"), "1d")
    rows = rows_of(c, "1d", NY)
    raw = {r[0]: r[4] for r in unadjusted(rows, c.splits, NY)}
    adj = {r[0]: r[4] for r in rows}
    before, after = ny_ms("2020-08-28"), ny_ms("2020-08-31")
    assert raw[before] == pytest.approx(adj[before] * 4)
    assert raw[after] == pytest.approx(adj[after])


def test_daily_time_is_exchange_midnight():
    rows = rows_of(parse_chart(fx("aapl_1d_2020split.json"), "1d"), "1d", NY)
    assert ny_ms("2020-08-31") in {r[0] for r in rows}


def test_closing_print_dropped():
    j = copy.deepcopy(fx("spy_1h_recent.json"))
    res = j["chart"]["result"][0]
    end = res["meta"]["currentTradingPeriod"]["regular"]["end"]
    res["timestamp"].append(end)
    for k in ("open", "high", "low", "close"):
        res["indicators"]["quote"][0][k].append(600.0)
    res["indicators"]["quote"][0]["volume"].append(0)
    rows = rows_of(parse_chart(j, "1h"), "60m", NY)
    close = dt.time(16, 0)
    # No zero-volume bar at or after the close survives (the recorded answer had one of its own too).
    assert not [r for r in rows if r[5] == 0 and dt.datetime.fromtimestamp(r[0] / 1000, NY).time() >= close]
    assert len(rows) >= 20  # the real hourly bars stay


class FakeYahoo:
    """Serves canned charts; counts calls."""

    def __init__(self, daily: Chart, hourly: Chart | None = None) -> None:
        self.daily, self.hourly, self.calls = daily, hourly, []

    async def chart(self, ysym, interval, period1, period2=None):
        self.calls.append((ysym, interval, period1))
        if ysym == "ZZZZ":
            raise NoData("no")
        return self.daily if interval == "1d" else self.hourly

    async def quotes(self, ysyms):
        return {}

    async def close(self):
        pass


class FakeBinance:
    async def pairs(self):
        return [("BTCUSDT", "BTC", "USDT")]

    async def chart(self, symbol, interval, period1, period2=None):
        return Chart([(1795651200, 60000.0, 61000.0, 59000.0, 60500.0, 100.0)], [], [], {"exchangeTimezoneName": "UTC", "instrumentType": "CRYPTOCURRENCY"})

    async def minute(self, symbol):
        return (1795737540000, 60480.0, 60520.0, 60470.0, 60500.0, 3.5)

    async def trading_day(self, symbols):
        return {s: {"symbol": s, "openPrice": "60000", "highPrice": "61000", "lowPrice": "59000", "lastPrice": "60500",
                    "volume": "100", "priceChange": "500", "priceChangePercent": "0.833", "openTime": 1795651200000,
                    "closeTime": 1795737599999} for s in symbols}

    async def close(self):
        pass


def chart(bars, splits=(), meta=None) -> Chart:
    return Chart(list(bars), list(splits), [], meta or {"exchangeTimezoneName": "America/New_York"})


async def test_new_split_rescales_and_refetches():
    t = lambda d: int(dt.datetime.combine(dt.date.fromisoformat(d), dt.time(9, 30), NY).timestamp())
    pre = chart([(t("2024-06-06"), 1200, 1210, 1190, 1200, 1e6), (t("2024-06-07"), 1200, 1210, 1190, 1200, 1e6)])
    y = FakeYahoo(pre, chart([(t("2024-06-07"), 1200, 1210, 1190, 1200, 1e5)]))
    s = Store(":memory:")
    b = Bars(s, lambda sym: (y, sym))
    await b.ensure("NVDA", "60m")
    assert s.bars("NVDA", "60m", 0, 2**62)[0][4] == 1200
    # Ten days later Yahoo's daily answer carries a 10-for-1 split, and the whole history comes adjusted.
    post = chart([(t("2024-06-07"), 120, 121, 119, 120, 1e7), (t("2024-06-10"), 121, 122, 120, 121, 1e7)], [(t("2024-06-10"), 10.0, 1.0)])
    y.daily = post
    s.mark_fetched("NVDA", "1d", 0)
    await b.ensure("NVDA", "1d")
    assert [r[4] for r in s.bars("NVDA", "1d", 0, 2**62)] == [120, 121]  # replaced whole, old 1200s gone
    h = s.bars("NVDA", "60m", 0, 2**62)[0]
    assert h[4] == pytest.approx(120) and h[5] == pytest.approx(1e6)  # intraday rescaled: price /10, volume x10
    assert s.series("NVDA", "60m").fetched_at == 0  # its tail is fetched again on the next ask


async def test_fresh_series_is_not_fetched_again():
    y = FakeYahoo(parse_chart(fx("aapl_1d_2020split.json"), "1d"))
    b = Bars(Store(":memory:"), lambda sym: (y, sym))
    await b.ensure("AAPL", "1d")
    await b.ensure("AAPL", "1d")
    assert len(y.calls) == 1


# ── symbols ──

def test_symbol_names():
    assert to_yahoo("BRK.B") == "BRK-B"
    assert to_yahoo("GSPC") == "^GSPC" and to_yahoo("SPX") == "^GSPC"
    assert clean_name("Apple Inc. - Common Stock") == "Apple Inc."
    assert clean_name("Berkshire Hathaway Inc. New Common Stock") == "Berkshire Hathaway Inc."


def test_listing_filters():
    nas = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n" \
          "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\nQQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N\n" \
          "ABCDW|Abcd Corp - Warrant|S|N|N|100|N|N\nZXZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N\nFile Creation Time: 1125202621:32|||||||\n"
    rows = parse_nasdaqlisted(nas)
    assert [(r.ticker, r.type) for r in rows] == [("AAPL", "stock"), ("QQQ", "etf")]
    oth = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n" \
          "BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B\nBAC$B|Bank of America Preferred B|N|BACpB|N|100|N|BAC-B\n" \
          "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
    rows = parse_otherlisted(oth)
    assert [(r.ticker, r.exchange, r.type) for r in rows] == [("BRK.B", "XNYS", "stock"), ("SPY", "ARCX", "etf")]


# ── the Market API ──

@pytest.fixture
async def client(tmp_path):
    c = parse_chart(fx("aapl_1d_2020split.json"), "1d")
    c.dividends = [(int(dt.datetime(2020, 8, 7, 13, 30, tzinfo=dt.timezone.utc).timestamp()), 0.205)]
    app = make_app(Store(":memory:"), FakeYahoo(c), tmp_path, FakeBinance())
    async with TestClient(TestServer(app)) as cl:
        yield cl


async def test_bars_answer(client):
    r = await client.get("/market/v1/bars/AAPL", params=dict(tf="1d", **{"from": "2020-01-01"}, to="2020-12-31", adjusted="true", order="desc", limit="3"))
    d = await r.json()
    assert r.status == 200 and d["truncated"] is True and len(d["t"]) == 3
    assert d["t"] == sorted(d["t"], reverse=True)  # newest first
    assert set(d) >= {"t", "o", "h", "l", "c", "v", "delayed", "source", "asOf"}


async def test_dividends_as_declared(client):
    d = await (await client.get("/market/v1/dividends", params={"ticker": "AAPL"})).json()
    # 0.205 split-adjusted, before the 4-for-1: declared 0.82.
    assert d["dividends"][0]["amount"] == pytest.approx(0.82)


async def test_unknown_and_unserved(client):
    r = await client.get("/market/v1/bars/ZZZZ", params=dict(tf="1d", **{"from": "2020-01-01"}, to="2020-12-31"))
    assert r.status == 404 and (await r.json())["error"]["code"] == "NOT_FOUND"
    r = await client.get("/market/v1/news")
    assert r.status == 404
    r = await client.get("/market/v1/bars/AAPL", params=dict(tf="1s", **{"from": "2020-01-01"}, to="2020-12-31"))
    assert (await r.json())["t"] == []  # seconds: never served


async def test_routes_by_source(client):
    # A crypto pair goes to Binance; its daily bar is a UTC day.
    d = await (await client.get("/market/v1/bars/BTCUSDT", params=dict(tf="1d", **{"from": "2026-11-01"}, to="2026-11-30"))).json()
    assert d["c"] == [60500.0] and d["t"] == [1795651200000]
    s = await (await client.get("/market/v1/snapshot", params={"tickers": "BTCUSDT"})).json()
    assert s["tickers"][0]["day"]["c"] == 60500.0 and s["tickers"][0]["changePct"] == pytest.approx(0.833)
    assert s["tickers"][0]["minute"] == {"t": 1795737540000, "o": 60480.0, "h": 60520.0, "l": 60470.0, "c": 60500.0, "v": 3.5}
    info = await (await client.get("/market/v1/tickers/BTCUSDT")).json()
    assert (info["type"], info["exchange"], info["currency"]) == ("crypto", "BINANCE", "USDT")
    assert (info["delayMinutes"], info["dataSource"]) == (0, "Binance")


def test_cfd_names_follow_the_futures():
    assert to_yahoo("US500") == "ES=F" and to_yahoo("US100") == "NQ=F" and to_yahoo("XAUUSD") == "GC=F"
    assert to_yahoo("SPX") == "^GSPC"  # the cash index keeps its name


async def test_dates_are_the_exchange_days(tmp_path):
    # A BIST daily bar is keyed on Istanbul midnight, the evening before in New York: from=<that date> includes it.
    ist = ZoneInfo("Europe/Istanbul")
    day = lambda d: int(dt.datetime.combine(dt.date.fromisoformat(d), dt.time(10), ist).timestamp())
    c = Chart([(day("2021-01-04"), 12.0, 12.8, 11.9, 12.68, 1e6), (day("2021-01-05"), 12.7, 12.9, 12.5, 12.58, 1e6)], [], [],
              {"exchangeTimezoneName": "Europe/Istanbul", "instrumentType": "EQUITY"})
    app = make_app(Store(":memory:"), FakeYahoo(c), tmp_path, FakeBinance())
    async with TestClient(TestServer(app)) as cl:
        d = await (await cl.get("/market/v1/bars/THYAO", params=dict(tf="1d", **{"from": "2021-01-04"}, to="2021-01-04"))).json()
    assert d["c"] == [12.68]


def _isy(d, c, adj, cap, h=None, lo=None, turnover=1e6):
    return {"HGDG_TARIH": d, "HG_KAPANIS": c, "HGDG_KAPANIS": adj, "SERMAYE": cap, "HG_MAX": h or c, "HG_MIN": lo or c,
            "HG_AOF": c, "HG_HACIM": turnover}


def test_bist_actions_from_is_yatirim():
    # CCOLA-like: an 11-for-1 bonus issue (capital x11, the ratio steps by 11) and a dividend (ratio step, no capital change).
    rows = [
        _isy("09-08-2024", 760.0, 760.0 / 11 / 1.02, 1e8),
        _isy("12-08-2024", 760.5, 760.5 / 11 / 1.02, 1e8),
        _isy("13-08-2024", 69.35, 69.35 / 1.02, 11e8),
        _isy("14-08-2024", 67.75, 67.75 / 1.02, 11e8),
        _isy("15-08-2024", 66.0, 66.0, 11e8),  # ex-dividend: ratio steps by 1.02 without a capital change
    ]
    splits, divs = actions(rows)
    assert splits == [(dt.date(2024, 8, 13), pytest.approx(11.0))]
    assert len(divs) == 1 and divs[0][0] == dt.date(2024, 8, 15) and divs[0][1] == pytest.approx(67.75 * 0.02 / 1.02)


def test_bist_open_from_yahoo_at_is_yatirim_level():
    rows = [_isy("12-08-2024", 760.5, 1.0, 1e8, h=770.0, lo=750.0, turnover=7.605e9)]
    # Yahoo's bar that day was (wrongly) adjusted by 11: its shape is kept, its level is Is Yatirim's.
    yb = {dt.date(2024, 8, 12): (68.0, 70.0, 68.0, 69.14, 1e6)}
    (d, o, h, lo, c, v), = daily_rows(rows, yb)
    assert c == 760.5 and o == pytest.approx(68.0 * 760.5 / 69.14) and v == pytest.approx(1e7)
    assert h >= max(o, c) and lo <= min(o, c)


def test_turkish_title_case():
    assert tr_title("TÜRK HAVA YOLLARI A.O.") == "Türk Hava Yolları A.O."
    assert tr_title("İŞ GİRİŞİM SERMAYESİ YATIRIM ORTAKLIĞI A.Ş.") == "İş Girişim Sermayesi Yatırım Ortaklığı A.Ş."


def test_spread_formulas():
    from chartd import spread as sp
    assert sp.app_name("XLFsPY") == "XLF/SPY"
    for f in ("XLF/SPY", "US10Y-US02Y", "(AAPL+MSFT)/2", "2*GLD-SLV", "-SPY+QQQ", "ES1!/NQ1!"):
        assert sp.is_formula(f), f
    for f in ("BRK.B", "ES1!", "AAPL", "(AAPL", "AAPL)", "XLF//SPY", "LINK.IS"):
        assert not sp.is_formula(f), f
    n = sp.parse("(AAPL+MSFT)/2")
    assert sp.legs(n) == ["AAPL", "MSFT"] and sp.evaluate(n, {"AAPL": 100, "MSFT": 300}) == 200
    assert sp.evaluate(sp.parse("2*GLD-SLV"), {"GLD": 10, "SLV": 3}) == 17
    assert sp.evaluate(sp.parse("-SPY+1"), {"SPY": 5}) == -4
    bars = sp.combine(sp.parse("A-B"), {"A": {1: (1, 10, 12, 9, 11, 0), 2: (2, 11, 11, 11, 11, 0)}, "B": {1: (1, 5, 6, 4, 5, 0)}}, lambda k: k)
    assert bars == [(1, 5, 6, 5, 6, 0.0)]  # only the common bar; h/l cover o/c


async def test_spread_route(client):
    d = await (await client.get("/market/v1/bars/AAPLsPY", params=dict(tf="1d", **{"from": "2020-08-25"}, to="2020-09-02"))).json()
    assert d["source"] == "spread" and d["c"] and all(c == pytest.approx(1.0) for c in d["c"])  # AAPL/SPY: the fake serves the same bars for both legs


def test_bars_are_made_sane():
    from chartd.bars import sane
    assert sane((1, 1.10, 1.09, 1.05, 1.07, 0)) == (1, 1.10, 1.10, 1.05, 1.07, 0)


async def test_closed_app_alerts(tmp_path):
    from chartd.alerts import Alerts, hit
    prices = {"AAPL": 100.0, "BTCUSDT": 60000.0}
    shown = []

    async def px(ts):
        return {t: prices[t] for t in ts if t in prices}

    async def notify(title, body):
        shown.append(title)

    a = Alerts(tmp_path, px, notify)
    a.set_rules([
        {"id": "up", "symbol": "NASDAQ:AAPL", "op": "crossing_up", "level": {"v": 101}, "frequency": "only_once", "name": "AAPL over 101"},
        {"id": "line", "symbol": "BINANCE:BTCUSDT", "op": "crossing_down", "level": {"p0": 59000, "t0": 1000, "slope": 1.0}, "frequency": "every_time"},
        {"id": "ind", "symbol": "NASDAQ:AAPL", "op": "moving_up", "level": {"v": 1}, "frequency": "only_once"},  # not ours: dropped
    ])
    assert [r["id"] for r in a.rules] == ["up", "line"]
    assert await a.check(now=1000) == []                     # first prices: nothing crossed yet
    prices["AAPL"] = 102.0
    prices["BTCUSDT"] = 58000.0                              # the line is at 59000 + 1 * (1100 - 1000) = 59100
    fired = await a.check(now=1100)
    assert sorted(f["alertId"] for f in fired) == ["line", "up"] and "AAPL over 101" in shown
    prices["AAPL"] = 100.0
    await a.check(now=1200)
    prices["AAPL"] = 103.0
    assert [f["alertId"] for f in await a.check(now=1300)] == []  # only once: done
    assert len(Alerts(tmp_path, px, notify).fired) == 2            # kept on disk
    assert hit("greater", None, 5, 4) and not hit("crossing", None, 5, 4)


def test_screener_state_steps_like_the_app():
    """State at D, stepped with S's bar by the app's own formulas (fields.rs), equals the state computed at S."""
    import numpy as np
    from chartd.screener import next_session, ticker_state
    rng = np.random.default_rng(7)
    days = [dt.date(2024, 1, 2)]
    while len(days) < 600:
        days.append(next_session(days[-1]))
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
    o = c * (1 + rng.normal(0, 0.005, len(days)))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.01, len(days))))
    lo = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.01, len(days))))
    v = rng.uniform(1e5, 1e6, len(days))
    D, S = days[-2], days[-1]
    a = ticker_state(days[:-1], o[:-1], h[:-1], lo[:-1], c[:-1], v[:-1], D, S, [], None, None)
    b = ticker_state(days, o, h, lo, c, v, S, next_session(S), [], None, None)
    p, hh, ll, vv, pc = c[-1], h[-1], lo[-1], v[-1], c[-2]
    k = 2 / 21
    assert a["EMA20"] + k * (p - a["EMA20"]) == pytest.approx(b["EMA20"])
    assert (a["SMA20|next"] + p) / 20 == pytest.approx(b["SMA20"])
    gain = (a["RSI|gain"] * 13 + max(p - pc, 0)) / 14
    loss = (a["RSI|loss"] * 13 + max(pc - p, 0)) / 14
    assert 100 - 100 / (1 + gain / loss) == pytest.approx(b["RSI"])
    tr = max(hh - ll, abs(hh - pc), abs(ll - pc))
    assert (a["ATR"] * 13 + tr) / 14 == pytest.approx(b["ATR"])
    assert (a["ADR|next"] + hh - ll) / 14 == pytest.approx(b["ADR"])
    assert max(a["High.1M|next"], hh) == pytest.approx(b["High.1M"])
    assert min(a["Low.3M|next"], ll) == pytest.approx(b["Low.3M"])
    assert 100 * (p / a["Perf.1M|next"] - 1) == pytest.approx(b["Perf.1M"])
    assert (a["average_volume_10d_calc|next"] + vv) / 10 == pytest.approx(b["average_volume_10d_calc"])
    assert a["volume|1W|next"] + vv == pytest.approx(b["volume|1W"])
    assert (a["Volatility.W|s"] + 100 * (hh - ll) / ll) / (a["Volatility.W|n"] + 1) == pytest.approx(b["Volatility.W"])
    assert 100 * (vv / a["volume_change|next"] - 1) == pytest.approx(b["volume_change"])


def test_trading_calendar():
    from chartd.screener import next_session, us_holidays
    assert next_session(dt.date(2026, 11, 25)) == dt.date(2026, 11, 27)  # Thanksgiving
    assert next_session(dt.date(2026, 12, 24)) == dt.date(2026, 12, 28)  # Christmas, then the weekend
    assert dt.date(2026, 4, 3) in us_holidays(2026)  # Good Friday
    assert dt.date(2026, 6, 19) in us_holidays(2026) and dt.date(2027, 7, 5) in us_holidays(2027)  # Juneteenth; July 4 on a Sunday


async def test_python_indicator(tmp_path, monkeypatch):
    (tmp_path / "ind").mkdir()
    (tmp_path / "ind" / "double.py").write_text("TITLE = 'Twice the close'\ndef compute(bars):\n    return bars['c'] * 2\n")
    (tmp_path / "ind" / "broken.py").write_text("def nope(): pass\n")
    monkeypatch.setenv("CHARTD_INDICATORS", str(tmp_path / "ind"))
    c = parse_chart(fx("aapl_1d_2020split.json"), "1d")
    app = make_app(Store(":memory:"), FakeYahoo(c), tmp_path, FakeBinance())
    async with TestClient(TestServer(app)) as cl:
        listing = (await (await cl.get("/chartdesk/v1/py")).json())["indicators"]
        assert [(x["id"], x["title"], x["error"] is None) for x in listing] == [("BROKEN", "broken", False), ("DOUBLE", "Twice the close", True)]
        d = await (await cl.get("/market/v1/bars/PY.DOUBLE.AAPL", params=dict(tf="1d", **{"from": "2020-08-25"}, to="2020-09-02"))).json()
        raw = await (await cl.get("/market/v1/bars/AAPL", params=dict(tf="1d", **{"from": "2020-08-25"}, to="2020-09-02"))).json()
        assert d["source"] == "python" and d["c"] == [2 * x for x in raw["c"]]


async def test_mcp_relay(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    c = parse_chart(fx("aapl_1d_2020split.json"), "1d")
    app = make_app(Store(":memory:"), FakeYahoo(c), tmp_path, FakeBinance())
    async with TestClient(TestServer(app)) as cl:
        token = (tmp_path / "cfg" / "chartdesk" / "mcp-token").read_text().strip()
        auth = {"Authorization": f"Bearer {token}"}
        ping = '{"jsonrpc":"2.0","id":7,"method":"ping"}'
        assert (await cl.post("/mcp", data=ping)).status == 401
        assert (await cl.post("/mcp", data=ping, headers={**auth, "Origin": "https://evil.example"})).status == 403
        closed = await (await cl.post("/mcp", data=ping, headers=auth)).json()
        assert closed["id"] == 7 and "not open" in closed["error"]["message"]
        ws = await cl.ws_connect("/chartdesk/v1/agent")

        async def window():
            m = await ws.receive_json()
            await ws.send_json({"id": m["id"], "reply": '{"jsonrpc":"2.0","id":7,"result":{}}'})

        task = asyncio.create_task(window())
        r = await cl.post("/mcp", data=ping, headers=auth)
        assert await r.json() == {"jsonrpc": "2.0", "id": 7, "result": {}}
        await task
        # A describing method answered once is answered from memory while the window is closed.
        listing = '{"jsonrpc":"2.0","id":8,"method":"tools/list"}'

        async def window2():
            m = await ws.receive_json()
            await ws.send_json({"id": m["id"], "reply": '{"jsonrpc":"2.0","id":8,"result":{"tools":[{"name":"get_chart"}]}}'})

        task = asyncio.create_task(window2())
        await cl.post("/mcp", data=listing, headers=auth)
        await task
        await ws.close()
        await asyncio.sleep(0.05)
        again = await (await cl.post("/mcp", data=listing.replace('"id":8', '"id":9'), headers=auth)).json()
        assert again == {"jsonrpc": "2.0", "id": 9, "result": {"tools": [{"name": "get_chart"}]}}
