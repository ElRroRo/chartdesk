/*
 * Chart Desk: TradingView's symbol names -> ours.
 *
 * The exchange prefix matters here, not only the ticker: the core picks a symbol's trading hours by its
 * exchange (CME_MINI trades nearly round the clock, INDEX keeps New York stock hours). So a broker CFD such as
 * OANDA:XAUUSD becomes the future it follows (COMEX:GC1!), FRED:<ID> an economic series, and an index the
 * INDEX exchange chartd lists it under. Names we serve as they are (BIST:THYAO, NYMEX:NG1!, TVC:DXY) pass.
 */
import * as kv from "../data/kv";

/** Ticker (whatever the exchange) -> our full name: CFD and spot names follow the futures, as the CFDs do. */
const BY_TICKER: Record<string, string> = {
  US500: "CME_MINI:ES1!", SPX500: "CME_MINI:ES1!", US100: "CME_MINI:NQ1!", NAS100: "CME_MINI:NQ1!", USTEC: "CME_MINI:NQ1!",
  US30: "CBOT_MINI:YM1!", US2000: "CME_MINI:RTY1!",
  USOIL: "NYMEX:CL1!", WTI: "NYMEX:CL1!", UKOIL: "NYMEX:BZ1!", BRENT: "NYMEX:BZ1!", NATGAS: "NYMEX:NG1!",
  XAUUSD: "COMEX:GC1!", GOLD: "COMEX:GC1!", XAGUSD: "COMEX:SI1!", SILVER: "COMEX:SI1!", COPPER: "COMEX:HG1!",
  CORN: "CBOT:ZC1!", SOYBEAN: "CBOT:ZS1!", WHEAT: "CBOT:ZW1!", COTTON: "ICEUS:CT1!", COFFEE: "ICEUS:KC1!",
  SUGAR: "ICEUS:SB1!", COCOA: "ICEUS:CC1!",
  BTCUSD: "BINANCE:BTCUSDT", ETHUSD: "BINANCE:ETHUSDT",
};
/** Full names whose exchange alone is TradingView's. */
const BY_SYMBOL: Record<string, string> = {
  "SP:SPX": "INDEX:SPX", "TVC:SPX": "INDEX:SPX", "NASDAQ:NDX": "INDEX:NDX", "TVC:NDX": "INDEX:NDX",
  "DJ:DJI": "INDEX:DJI", "TVC:DJI": "INDEX:DJI", "TVC:RUT": "INDEX:RUT", "RUSSELL:RUT": "INDEX:RUT",
  "CBOE:VIX": "INDEX:VIX", "TVC:VIX": "INDEX:VIX", "NASDAQ:IXIC": "INDEX:COMP", "TVC:IXIC": "INDEX:COMP",
  "NASDAQ:SOX": "INDEX:SOX", "TVC:NYA": "INDEX:NYA",
  // NYMEX's financially settled cotton follows ICE's cotton.
  "NYMEX:TT1!": "ICEUS:CT1!",
};
/** FX pairs chartd serves (TradingView files them under FX, FX_IDC, OANDA, a broker...). */
const FX = new Set([
  "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "EURGBP", "EURJPY", "GBPJPY", "EURCHF",
  "USDTRY", "EURTRY", "GBPTRY", "CHFTRY", "JPYTRY", "USDCNH", "USDMXN", "USDZAR", "USDBRL", "USDINR", "USDKRW",
  "USDSEK", "USDNOK", "USDPLN", "USDHUF", "USDRUB", "USDSGD", "USDHKD", "USDILS",
]);

/** A TradingView symbol in our names (unchanged when we serve it as it is, or not at all). */
export function tvSymbol(full: string): string {
  const s = full.trim().toUpperCase();
  if (BY_SYMBOL[s]) return BY_SYMBOL[s];
  const i = s.indexOf(":");
  const ex = i < 0 ? "" : s.slice(0, i);
  const t = i < 0 ? s : s.slice(i + 1);
  if (ex === "FRED") return `ECONOMICS:${t}`;
  if (BY_TICKER[t]) return BY_TICKER[t];
  // A broker's dated rough-rice CFD (CMCMARKETS:ROUGHRICEK2026) follows the front future.
  if (/^ROUGHRICE/.test(t)) return "CBOT:ZR1!";
  if (FX.has(t) && ex !== "FX") return `FX:${t}`;
  return s;
}

/** Rewrite TradingView names already saved (watchlists, saved layouts). Idempotent; returns how many changed. */
export function migrateTvSymbols(): number {
  let n = 0;
  const fix = (s: unknown): unknown => {
    if (typeof s !== "string" || !s.includes(":")) return s;
    const t = tvSymbol(s);
    if (t !== s.trim().toUpperCase() && t !== s) n++;
    return t === s.trim().toUpperCase() ? s : t;
  };
  const wl = read("ot:watchlist:lists:v1");
  if (wl?.lists) {
    for (const l of wl.lists) {
      for (const g of l.groups ?? []) for (const r of g.rows ?? []) {
        const t = fix(r.ticker) as string;
        if (t !== r.ticker) { r.ticker = t; r.short = t.slice(t.indexOf(":") + 1); }
      }
      for (const r of l.extras ?? []) {
        const t = fix(r.ticker) as string;
        if (t !== r.ticker) { r.ticker = t; r.short = t.slice(t.indexOf(":") + 1); }
      }
    }
    if (n) kv.setItem("ot:watchlist:lists:v1", JSON.stringify(wl));
  }
  const before = n;
  const layouts = read("ot:layouts");
  if (Array.isArray(layouts)) {
    for (const l of layouts) for (const p of l?.snapshot?.panes ?? []) p.symbol = fix(p.symbol);
    if (n > before) kv.setItem("ot:layouts", JSON.stringify(layouts));
  }
  return n;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function read(key: string): any {
  try {
    const v = kv.getItem(key);
    return v ? JSON.parse(v) : null;
  } catch {
    return null;
  }
}
