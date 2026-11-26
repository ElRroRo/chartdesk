"""Symbol names: the app's ticker <-> Yahoo's, the indices and their broker aliases, and the US listing.

The app names a ticker as the Market API does ("AAPL", "BRK.B", "SPX"). Yahoo writes class shares with a
dash ("BRK-B") and indices with a caret ("^GSPC").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Index tickers -> (Yahoo symbol, name).
INDICES: dict[str, tuple[str, str]] = {
    "SPX": ("^GSPC", "S&P 500 Index"),
    "NDX": ("^NDX", "Nasdaq-100 Index"),
    "DJI": ("^DJI", "Dow Jones Industrial Average"),
    "RUT": ("^RUT", "Russell 2000 Index"),
    "VIX": ("^VIX", "CBOE Volatility Index"),
    "COMP": ("^IXIC", "Nasdaq Composite Index"),
    "SOX": ("^SOX", "PHLX Semiconductor Index"),
    "NYA": ("^NYA", "NYSE Composite Index"),
}
#: Continuous front-month futures (TradingView's "1!" names) -> (Yahoo symbol, name, exchange as TradingView names it).
FUTURES: dict[str, tuple[str, str, str]] = {
    "ES1!": ("ES=F", "E-mini S&P 500 Futures", "CME_MINI"),
    "NQ1!": ("NQ=F", "E-mini Nasdaq-100 Futures", "CME_MINI"),
    "RTY1!": ("RTY=F", "E-mini Russell 2000 Futures", "CME_MINI"),
    "YM1!": ("YM=F", "E-mini Dow Futures", "CBOT_MINI"),
    "CL1!": ("CL=F", "Crude Oil (WTI) Futures", "NYMEX"),
    "BZ1!": ("BZ=F", "Brent Crude Oil Futures", "NYMEX"),
    "NG1!": ("NG=F", "Natural Gas Futures", "NYMEX"),
    "RB1!": ("RB=F", "RBOB Gasoline Futures", "NYMEX"),
    "HO1!": ("HO=F", "Heating Oil Futures", "NYMEX"),
    "GC1!": ("GC=F", "Gold Futures", "COMEX"),
    "SI1!": ("SI=F", "Silver Futures", "COMEX"),
    "HG1!": ("HG=F", "Copper Futures", "COMEX"),
    "PL1!": ("PL=F", "Platinum Futures", "NYMEX"),
    "ZT1!": ("ZT=F", "2-Year T-Note Futures", "CBOT"),
    "ZF1!": ("ZF=F", "5-Year T-Note Futures", "CBOT"),
    "ZN1!": ("ZN=F", "10-Year T-Note Futures", "CBOT"),
    "ZB1!": ("ZB=F", "30-Year T-Bond Futures", "CBOT"),
    "ZC1!": ("ZC=F", "Corn Futures", "CBOT"),
    "ZS1!": ("ZS=F", "Soybean Futures", "CBOT"),
    "ZW1!": ("ZW=F", "Wheat Futures", "CBOT"),
    "KE1!": ("KE=F", "KC HRW Wheat Futures", "CBOT"),
    "ZL1!": ("ZL=F", "Soybean Oil Futures", "CBOT"),
    "ZM1!": ("ZM=F", "Soybean Meal Futures", "CBOT"),
    "ZR1!": ("ZR=F", "Rough Rice Futures", "CBOT"),
    "CT1!": ("CT=F", "Cotton No. 2 Futures", "ICEUS"),
    "KC1!": ("KC=F", "Coffee C Futures", "ICEUS"),
    "SB1!": ("SB=F", "Sugar No. 11 Futures", "ICEUS"),
    "CC1!": ("CC=F", "Cocoa Futures", "ICEUS"),
}
#: Other symbols TradingView files under TVC.
TVC: dict[str, tuple[str, str]] = {
    "DXY": ("DX-Y.NYB", "U.S. Dollar Index"),
}
#: US Treasury constant-maturity yields (FRED, daily, in percent), as TradingView names them.
YIELDS: dict[str, tuple[str, str]] = {
    "US01M": ("DGS1MO", "US 1 Month Treasury Yield"), "US03M": ("DGS3MO", "US 3 Month Treasury Yield"),
    "US06M": ("DGS6MO", "US 6 Month Treasury Yield"), "US01Y": ("DGS1", "US 1 Year Treasury Yield"),
    "US02Y": ("DGS2", "US 2 Year Treasury Yield"), "US03Y": ("DGS3", "US 3 Year Treasury Yield"),
    "US05Y": ("DGS5", "US 5 Year Treasury Yield"), "US07Y": ("DGS7", "US 7 Year Treasury Yield"),
    "US10Y": ("DGS10", "US 10 Year Treasury Yield"), "US20Y": ("DGS20", "US 20 Year Treasury Yield"),
    "US30Y": ("DGS30", "US 30 Year Treasury Yield"),
}
#: Borsa Istanbul indices (Yahoo: <code>.IS).
BIST_INDICES: dict[str, str] = {
    "XU100": "BIST 100", "XU030": "BIST 30", "XU050": "BIST 50", "XUTUM": "BIST All Shares",
    "XBANK": "BIST Banks", "XUSIN": "BIST Industrials", "XUMAL": "BIST Financials", "XUHIZ": "BIST Services",
    "XUTEK": "BIST Technology", "XHOLD": "BIST Holding and Investment", "XGIDA": "BIST Food, Beverage",
    "XKMYA": "BIST Chemical, Petroleum, Plastic", "XMANA": "BIST Basic Metal", "XELKT": "BIST Electricity",
    "XULAS": "BIST Transportation", "XTRZM": "BIST Tourism", "XGMYO": "BIST Real Estate Investment Trusts",
    "XKTUM": "BIST Participation All Shares",
}
#: FX pairs (Yahoo: <pair>=X).
FX_PAIRS: list[str] = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "EURGBP", "EURJPY", "GBPJPY", "EURCHF",
    "USDTRY", "EURTRY", "GBPTRY", "CHFTRY", "JPYTRY", "USDCNH", "USDMXN", "USDZAR", "USDBRL", "USDINR", "USDKRW",
    "USDSEK", "USDNOK", "USDPLN", "USDHUF", "USDRUB", "USDSGD", "USDHKD", "USDILS",
]
#: Names people type for the same things. Broker CFD names (US500, USOIL, XAUUSD...) follow the futures, as the
#: CFDs do (they trade overnight); the cash indices keep their own names (SPX, NDX).
ALIASES: dict[str, str] = {
    "GSPC": "SPX", "IXIC": "COMP",
    "US500": "ES1!", "SPX500": "ES1!", "US100": "NQ1!", "NAS100": "NQ1!", "USTEC": "NQ1!",
    "US30": "YM1!", "US2000": "RTY1!",
    "USOIL": "CL1!", "WTI": "CL1!", "UKOIL": "BZ1!", "BRENT": "BZ1!", "NATGAS": "NG1!",
    "XAUUSD": "GC1!", "GOLD": "GC1!", "XAGUSD": "SI1!", "SILVER": "SI1!", "COPPER": "HG1!",
    "CORN": "ZC1!", "SOYBEAN": "ZS1!", "WHEAT": "ZW1!", "COTTON": "CT1!", "COFFEE": "KC1!", "SUGAR": "SB1!", "COCOA": "CC1!",
}
#: nasdaqtrader exchange letters (otherlisted.txt) -> MIC.
EXCHANGES = {"N": "XNYS", "A": "XASE", "P": "ARCX", "Z": "BATS", "V": "IEXG"}


def base(ticker: str) -> str:
    """The ticker an alias stands for (or the ticker itself)."""
    return ALIASES.get(ticker, ticker)


def to_yahoo(ticker: str) -> str:
    t = base(ticker.upper())
    if t in INDICES:
        return INDICES[t][0]
    if t in FUTURES:
        return FUTURES[t][0]
    if t in TVC:
        return TVC[t][0]
    if t in BIST_INDICES:
        return f"{t}.IS"
    if t in FX_PAIRS:
        return f"{t}=X"
    return t.replace(".", "-")


def is_index(ticker: str) -> bool:
    return base(ticker) in INDICES


@dataclass(frozen=True)
class Listing:
    ticker: str
    name: str
    type: str  # "stock" | "etf" | "index" | "futures" | "crypto"
    exchange: str
    market: str  # "stocks" | "indices" | "futures" | "crypto"


# Words that mark a listing nobody charts: warrants, units, rights, preferreds, notes.
_SKIP = re.compile(r"\b(warrants?|units?|rights?|preferred|depositary shares? each representing|notes due|debentures?)\b", re.I)
# Share-class boilerplate after the company name.
_TAIL = re.compile(
    r"\s*(-\s*)?(class [a-z] )?(common stock|ordinary shares|common shares|american depositary shares|"
    r"shares of beneficial interest|common units).*$",
    re.I,
)


def clean_name(name: str) -> str:
    n = _TAIL.sub("", name.strip())
    n = n.split(" - ")[0].strip()
    n = re.sub(r"\s+New$", "", n)  # "Berkshire Hathaway Inc. New": the listing's marker, not the name
    return n or name.strip()


def parse_nasdaqlisted(text: str) -> list[Listing]:
    out = []
    for line in text.splitlines()[1:]:
        f = line.split("|")
        if len(f) < 8 or line.startswith("File Creation Time"):
            continue
        sym, name, _cat, test, _fin, _lot, etf = f[:7]
        if test == "Y" or not sym or _SKIP.search(name):
            continue
        out.append(Listing(sym, clean_name(name), "etf" if etf == "Y" else "stock", "XNAS", "stocks"))
    return out


def parse_otherlisted(text: str) -> list[Listing]:
    out = []
    for line in text.splitlines()[1:]:
        f = line.split("|")
        if len(f) < 8 or line.startswith("File Creation Time"):
            continue
        sym, name, exch, _cqs, etf, _lot, test = f[:7]
        if test == "Y" or not sym or "$" in sym or _SKIP.search(name):
            continue
        out.append(Listing(sym, clean_name(name), "etf" if etf == "Y" else "stock", EXCHANGES.get(exch, "XNYS"), "stocks"))
    return out


def index_listings() -> list[Listing]:
    """Indices, futures and their aliases."""
    out = [Listing(t, name, "index", "INDEX", "indices") for t, (_y, name) in INDICES.items()]
    out += [Listing(t, name, "futures", ex, "futures") for t, (_y, name, ex) in FUTURES.items()]
    for a, t in ALIASES.items():
        if t in INDICES:
            out.append(Listing(a, f"{INDICES[t][1]} ({a})", "index", "INDEX", "indices"))
        else:
            _y, name, ex = FUTURES[t]
            out.append(Listing(a, f"{name} ({a})", "futures", ex, "futures"))
    return out


def other_listings() -> list[Listing]:
    """DXY, the Treasury yields, BIST indices and FX."""
    out = [Listing(t, name, "index", "TVC", "indices") for t, (_y, name) in TVC.items()]
    out += [Listing(t, name, "bond", "TVC", "bonds") for t, (_f, name) in YIELDS.items()]
    out += [Listing(t, name, "index", "BIST", "indices") for t, name in BIST_INDICES.items()]
    out += [Listing(p, f"{p[:3]} / {p[3:]}", "forex", "FX", "fx") for p in FX_PAIRS]
    return out


_TR_UP = str.maketrans("iı", "İI")
_TR_LO = str.maketrans("Iİ", "ıi")


def tr_title(name: str) -> str:
    """KAP's capitals in Turkish title case ("TÜRK HAVA YOLLARI A.O." -> "Türk Hava Yolları A.O."); a word with a
    dot in it (A.Ş., A.O., T.A.Ş.) stays in capitals."""
    out = []
    for w in name.split():
        if "." in w or len(w) <= 1:
            out.append(w)
            continue
        lo = w.translate(_TR_LO).lower()
        out.append(lo[0].translate(_TR_UP).upper() + lo[1:])
    return " ".join(out)


def parse_kap(items: list[dict], us: set[str]) -> list[Listing]:
    """KAP's roster of listed companies -> BIST stocks. A code a US listing already uses gets ".IS"."""
    out, seen = [], set()
    for e in items:
        for c in (e.get("stockCode") or "").split(","):
            c = c.strip().upper()
            if not c or c == "-" or c in seen:
                continue
            seen.add(c)
            name = tr_title((e.get("kapMemberTitle") or c).strip())
            out.append(Listing(f"{c}.IS" if c in us else c, name, "stock", "BIST", "stocks"))
    return out


def crypto_listings(pairs: list[tuple[str, str, str]]) -> list[Listing]:
    return [Listing(sym, f"{b} / {q}", "crypto", "BINANCE", "crypto") for sym, b, q in pairs]
