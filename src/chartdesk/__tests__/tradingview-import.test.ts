import { describe, expect, it, beforeEach, vi } from "vitest";

// kv falls back to localStorage outside the desktop shell.
const mem = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, String(v)),
  removeItem: (k: string) => void mem.delete(k), clear: () => mem.clear(), key: (i: number) => [...mem.keys()][i] ?? null,
  get length() { return mem.size; },
});
import {
  applyTvBundle, draftFromTv, drawingStyleFromTv, indicatorDefaultsFromTv, layoutFromTv, opaque, transparencyOf, tvScriptOf, watchlistsFromTv,
} from "../tradingview-import";
import { makeDefaultDraft } from "../../window/header/chart-settings";
import { migrateTvSymbols, tvSymbol } from "../tv-symbols";

const CP = {
  timezone: "Europe/Istanbul",
  paneProperties: {
    backgroundType: "solid", background: "#131722", gridLinesMode: "none",
    vertGridProperties: { style: 0, color: "rgba(240, 243, 250, 0.06)" }, horzGridProperties: { style: 0, color: "rgba(240, 243, 250, 0.06)" },
    crossHairProperties: { style: 2, width: 1, color: "#9598A1" }, topMargin: 12, bottomMargin: 6,
    legendProperties: { showStudyArguments: false, showStudyTitles: true, showLogo: false, showBackground: true, backgroundTransparency: 30 },
  },
  scalesProperties: { fontSize: 13, scaleSeriesOnly: true, textColor: "#B2B5BE", lineColor: "rgba(240, 243, 250, 0)", showSeriesLastValue: true, seriesLastValueMode: 1 },
};
const MSP = {
  style: 1, showCountdown: false, dividendsAdjustment: true, showPriceLine: true,
  candleStyle: { drawWick: true, drawBorder: false, drawBody: true, upColor: "#26a69a", downColor: "#ef5350", borderUpColor: "#26a69a", borderDownColor: "#ef5350", wickUpColor: "#26a69a", wickDownColor: "#ef5350", barColorsOnPrevClose: false },
};

describe("TradingView chart properties -> Settings draft", () => {
  const d = draftFromTv(CP, MSP, "dark");
  it("takes the canvas colours", () => {
    expect(d["canvas:Background"].controls[1]).toMatchObject({ kind: "color", color: "#131722" });
    expect(d["canvas:Vertical grid lines"].checked).toBe(false);
    expect(d["canvas:Horizontal grid lines"].checked).toBe(false);
    expect(d["canvas:Text"].controls[0]).toMatchObject({ color: "#B2B5BE" });
    expect(d["canvas:Text"].controls[1]).toMatchObject({ kind: "select", value: "13" });
    expect(d["canvas:Top"].controls[0]).toMatchObject({ value: "12" });
  });
  it("converts TV's line style enum (2 = dashed) to the dialog's (1 = dashed)", () => {
    expect(d["canvas:Crosshair"].controls[0]).toMatchObject({ color: "#9598A1", style: 1, width: 1 });
  });
  it("takes the status line and scales", () => {
    expect(d["legend:Inputs"].checked).toBe(false);
    expect(d["legend:Logo"].checked).toBe(false);
    expect(d["legend:Background"].controls[0]).toMatchObject({ kind: "slider", value: 30 });
    expect(d["scales:Scale price chart only"].checked).toBe(true);
    expect(d["scales:Countdown to bar close"].checked).toBe(false);
    expect(d["symbol:Adjust data for dividends"].checked).toBe(true);
    expect(d["symbol:Timezone"].controls[0]).toMatchObject({ value: "(UTC+3) Istanbul" });
  });
  it("takes the candle colours", () => {
    expect(d["style.candle:Body"].controls[0]).toMatchObject({ up: "#26a69a", down: "#ef5350" });
    expect(d["style.candle:Borders"].checked).toBe(false);
  });
  it("leaves rows TV did not set at the factory values", () => {
    const f = makeDefaultDraft("dark");
    expect(d["canvas:Watermark"]).toEqual(f["canvas:Watermark"]);
    expect(d["style.bar:Up color"]).toEqual(f["style.bar:Up color"]);
  });
});

describe("drawing tool defaults", () => {
  it("maps line tools (TV 1 = dotted)", () => {
    expect(drawingStyleFromTv("trend-line", { linecolor: "rgba(255, 255, 255, 1)", linewidth: 1, linestyle: 1, textcolor: "#089981" }))
      .toEqual({ color: "rgba(255, 255, 255, 1)", width: 1, lineStyle: "dotted", textColor: "#089981" });
    expect(drawingStyleFromTv("horizontal-ray", { linecolor: "#fff", linewidth: 2, showPrice: true })).toMatchObject({ width: 2, showPriceLabels: true });
  });
  it("turns a fill's alpha into the transparency", () => {
    expect(opaque("rgba(76, 175, 80, 0.2)")).toBe("#4caf50");
    expect(transparencyOf("rgba(76, 175, 80, 0.2)")).toBe(80);
    expect(drawingStyleFromTv("rectangle", { color: "rgba(242, 54, 69, 0)", backgroundColor: "rgba(76, 175, 80, 0.2)", linewidth: 1 }))
      .toMatchObject({ color: "rgba(242, 54, 69, 0)", backgroundColor: "#4caf50", transparency: 80, width: 1 });
  });
  it("keeps the fib level ladder", () => {
    const s = drawingStyleFromTv("fib-retracement", { level1: { coeff: 0, color: "#787B86", visible: true }, level2: { coeff: 0.618, color: "#089981", visible: false, linestyle: 2 } });
    expect(s.levels).toEqual([{ coeff: 0, color: "#787B86", visible: true }, { coeff: 0.618, color: "#089981", visible: false, style: "dashed" }]);
  });
});

describe("indicator defaults", () => {
  it("reads TV study keys and versions", () => {
    expect(tvScriptOf("study_Script$STD;RSI@tv-scripting_35_0")).toEqual(["STD;RSI", 35]);
    expect(tvScriptOf("Script$STD;MA%Ribbon@tv-scripting-101[v.13.0]")).toEqual(["STD;MA%Ribbon", 13]);
  });
  it("maps positional inputs and plot styles, newest version winning", () => {
    const out = indicatorDefaultsFromTv({
      "study_Script$STD;Bollinger_Bands@tv-scripting_31_0": JSON.stringify({ inputs: { in_0: 100, in_3: 1 } }),
      "study_Script$STD;Bollinger_Bands@tv-scripting_32_0": JSON.stringify({ inputs: { in_0: 125, in_1: "SMA", in_2: "close", in_3: 2, in_4: 0 }, styles: { plot_0: { color: "rgba(255, 255, 255, 1)", linewidth: 1, display: 4294967295 }, plot_1: { display: 0 } } }),
      "study_Script$STD;RSI@tv-scripting_35_0": JSON.stringify({ inputs: { in_0: 14, in_1: "close", in_2: "SMA", in_3: 14, in_4: 2, in_5: false } }),
      "study_Script$PUB;abc@tv-scripting_6_0": JSON.stringify({ inputs: { in_0: 1 } }),
    });
    expect(out.bb.inputs).toEqual({ length: 125, maType: "SMA", src: "close", mult: 2, offset: 0 });
    expect(out.bb.styles).toEqual({ plot0: { color: "rgba(255, 255, 255, 1)", lineWidth: 1, visible: true }, plot1: { visible: false } });
    expect(out.rsi.inputs).toEqual({ length: 14, src: "close", maType: "SMA", maLength: 14, bbMult: 2, calculateDivergence: false });
    expect(Object.keys(out).sort()).toEqual(["bb", "rsi"]);
  });
});

describe("watchlists and layouts", () => {
  it("keeps sections, flags and order", () => {
    const [l] = watchlistsFromTv([{ name: "Inputs", color: "red", symbols: ["###Energy", "NYMEX:NG1!", "BLACKBULL:BRENT", "###Metals", "OANDA:XAUUSD"] }]);
    expect(l.flag).toBe("red");
    expect(l.groups.map((g: { name: string }) => g.name)).toEqual(["Energy", "Metals"]);
    expect(l.groups[0].rows.map((r: { ticker: string }) => r.ticker)).toEqual(["NYMEX:NG1!", "NYMEX:BZ1!"]);
  });
  it("turns a TV layout into a saved layout with mapped studies", () => {
    const report = { applied: [], skipped: [] as string[] };
    const content = { charts: [{ chartProperties: CP, panes: [{ sources: [
      { type: "MainSeries", state: { ...MSP, symbol: "NASDAQ:FTDR", interval: "1D", style: 9 } },
      { type: "Study", metaInfo: "Script$STD;Bollinger_Bands@tv-scripting-101[v.35.0]", state: { inputs: { in_0: 125, in_3: 2 } } },
      { type: "Study", metaInfo: "Script$PUB;xyz@tv-scripting-101[v.2.0]", state: { shortTitle: "Power Law" } },
      { type: "study_Volume", state: { inputs: { length: 20 }, palettes: { volumePalette: { colors: { 0: { color: "rgba(41, 98, 255, 0.5)" }, 1: { color: "rgba(255, 255, 255, 0.5)" } } } } } },
    ] }] }] };
    const l = layoutFromTv("FTDR", content, makeDefaultDraft("dark"), report)!;
    const p = l.snapshot.panes[0];
    expect(p).toMatchObject({ symbol: "NASDAQ:FTDR", interval: "1D", chartType: "hollowCandle", indicators: ["bb", "volume"] });
    expect(p.indicatorSettings.bb.inputs).toEqual({ length: 125, mult: 2 });
    expect(p.indicatorSettings.volume.styles.vol.palette).toEqual(["rgba(41, 98, 255, 0.5)", "rgba(255, 255, 255, 0.5)"]);
    expect(report.skipped).toEqual(['layout "FTDR": indicator Power Law not available']);
  });
});

describe("applyTvBundle", () => {
  beforeEach(() => localStorage.clear());
  it("writes every store once and reports", () => {
    const r = applyTvBundle({ version: 1, id: "t1", theme: "dark", chartproperties: CP, mainSeriesProperties: MSP,
      settings: { linetoolhorzline: JSON.stringify({ linewidth: 3, linecolor: "rgba(76, 175, 80, 1)" }), linetoolnope: "{}" },
      watchlists: [{ name: "Watchlist", symbols: ["NYSE:SCHW", "TVC:DXY"] }] });
    expect(localStorage.getItem("ot:theme")).toBe("dark");
    expect(JSON.parse(localStorage.getItem("ot:timezone")!)).toEqual({ label: "(UTC+3) Istanbul", iana: "Europe/Istanbul" });
    expect(JSON.parse(localStorage.getItem("ot:drawing-kind-defaults")!)["horizontal-line"]).toEqual({ color: "rgba(76, 175, 80, 1)", width: 3 });
    const wl = JSON.parse(localStorage.getItem("ot:watchlist:lists:v1")!);
    expect(wl.activeId).toBe("tv-watchlist");
    expect(wl.lists[0].groups[0].rows.map((x: { ticker: string }) => x.ticker)).toEqual(["NYSE:SCHW", "TVC:DXY"]);
    expect(JSON.parse(localStorage.getItem("ot:chart-settings-defaults")!).draft["canvas:Background"].controls[1].color).toBe("#131722");
    expect(r.skipped).toEqual(["drawing tool linetoolnope: no matching tool"]);
  });
});

describe("TradingView symbol names", () => {
  it("CFDs follow the futures, FRED is economics, indices get our exchange", () => {
    expect(tvSymbol("OANDA:XAUUSD")).toBe("COMEX:GC1!");
    expect(tvSymbol("CAPITALCOM:US500")).toBe("CME_MINI:ES1!");
    expect(tvSymbol("BLACKBULL:BRENT")).toBe("NYMEX:BZ1!");
    expect(tvSymbol("FRED:IORB")).toBe("ECONOMICS:IORB");
    expect(tvSymbol("SP:SPX")).toBe("INDEX:SPX");
    expect(tvSymbol("CMCMARKETS:ROUGHRICEK2026")).toBe("CBOT:ZR1!");
    expect(tvSymbol("OANDA:EURUSD")).toBe("FX:EURUSD");
    expect(tvSymbol("BIST:THYAO")).toBe("BIST:THYAO");
    expect(tvSymbol("NYSE:SCHW")).toBe("NYSE:SCHW");
  });
  it("rewrites saved watchlists once", () => {
    localStorage.clear();
    localStorage.setItem("ot:watchlist:lists:v1", JSON.stringify({ lists: [{ id: "x", groups: [{ name: "G", rows: [{ ticker: "OANDA:XAUUSD", short: "XAUUSD" }, { ticker: "BIST:THYAO", short: "THYAO" }] }], extras: [] }] }));
    expect(migrateTvSymbols()).toBe(1);
    expect(migrateTvSymbols()).toBe(0);
    const rows = JSON.parse(localStorage.getItem("ot:watchlist:lists:v1")!).lists[0].groups[0].rows;
    expect(rows.map((r: { ticker: string; short: string }) => [r.ticker, r.short])).toEqual([["COMEX:GC1!", "GC1!"], ["BIST:THYAO", "THYAO"]]);
  });
});
