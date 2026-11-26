"""Live smoke test against a running chartd (127.0.0.1:8798): every market, every interval.

Checks each answer for: bars present, times ascending and unique, high >= max(open, close), low <= min(open,
close), prices > 0 (spreads and yields may be <= 0), and the newest bar not older than the market allows.
Usage: uv run python tools/smoke_live.py
"""
import datetime as dt
import json
import sys
import urllib.request

B = "http://127.0.0.1:8798/market/v1"
SYMS = {
    "AAPL": "stock", "BRK.B": "stock", "SPY": "etf", "SPX": "index", "NDX": "index", "VIX": "index",
    "ES1!": "fut", "CL1!": "fut", "GC1!": "fut", "ZC1!": "fut", "CT1!": "fut", "US500": "fut",
    "EURUSD": "fx", "USDTRY": "fx", "DXY": "idx24", "BTCUSDT": "crypto", "ETHUSDT": "crypto", "BTCTRY": "crypto",
    "THYAO": "bist", "CCOLA": "bist", "XU100": "bist", "US10Y": "yield", "US02Y": "yield",
    "XLFsPY": "spread", "US10Y-US02Y": "spread",
}
TFS = ["1m", "5m", "15m", "60m", "1d"]
DAILY_ONLY = {"yield"}


def get(path):
    try:
        with urllib.request.urlopen(B + path, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def main():
    today = dt.date.today()
    bad = 0
    for s, kind in SYMS.items():
        line = []
        for tf in TFS:
            if kind in DAILY_ONLY and tf != "1d":
                continue
            if kind == "spread" and "US10Y" in s and tf != "1d":
                continue
            frm = (today - dt.timedelta(days=4000 if tf == "1d" else 6)).isoformat()
            j = get(f"/bars/{s}?tf={tf}&from={frm}&to={today.isoformat()}&adjusted=true&order=asc&limit=50000")
            t, o, h, lo, c = (j.get(k, []) for k in "tohlc")
            probs = []
            if not t:
                probs.append(f"empty {j.get('error', '')}")
            else:
                if any(b <= a for a, b in zip(t, t[1:])):
                    probs.append("times not ascending/unique")
                if any(hh < max(oo, cc) - 1e-9 or ll > min(oo, cc) + 1e-9 for oo, hh, ll, cc in zip(o, h, lo, c)):
                    probs.append("h/l outside o/c")
                # Spreads, yields and crude oil (WTI settled at -37.63 on 2020-04-20) may go to zero or below.
                if kind not in ("spread", "yield") and s != "CL1!" and any(x <= 0 for x in c):
                    probs.append("price <= 0")
                age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromtimestamp(t[-1] / 1000, dt.timezone.utc)).days
                if age > (5 if tf == "1d" else 4):
                    probs.append(f"newest bar {age} days old")
            bad += bool(probs)
            line.append(f"{tf}:{len(t)}" + (f" !! {'; '.join(probs)}" if probs else ""))
        print(f"{s:14} {' | '.join(line)}")
    print("problems:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
