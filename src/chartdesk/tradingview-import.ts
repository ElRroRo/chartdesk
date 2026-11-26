/*
 * Chart Desk: import a TradingView setup (chart settings, theme, timezone,
 * drawing-tool defaults, indicator defaults, watchlists, layouts).
 *
 * The bundle is captured from the owner's TradingView desktop app by
 * tools/tradingview-capture (read only) and served by the local server at
 * BUNDLE_URL. It runs once per bundle id, at boot, after kv is hydrated and
 * before App's modules seed their stores from kv, so everything below writes
 * the stores' persisted shapes directly.
 *
 * Mapping notes
 *  - TV line style enum is 0 solid / 1 dotted / 2 dashed. The chart Settings
 *    dialog's is 0 solid / 1 dashed / 2 dotted; the drawing core uses names.
 *  - TV colours carry their alpha in rgba(); the drawing core fills with
 *    `backgroundColor` at (100 - transparency)%, so a fill alpha becomes the
 *    transparency and the colour goes opaque (what its dialog shows).
 *  - TV indicator inputs are positional (in_0..); each mapped indicator lists
 *    the registry input ids in TV's order, per script version where they
 *    differ. Anything unmapped is reported, not guessed.
 */
import { migrateTvSymbols, tvSymbol } from "./tv-symbols";
import * as kv from "../data/kv";
import { cloneDraft, makeDefaultDraft, SETTINGS_FINGERPRINT, SETTINGS_REV, type CtrlValue, type Draft } from "../window/header/chart-settings";
import { TIMEZONES } from "../data/timezones";
import type { ChartTypeId } from "../window/chart/chart-types";
import { defaultIndicatorOptions } from "../window/chart/indicators/indicator-options";

export const BUNDLE_URL = "/chartdesk/tradingview-import.json";
const DONE_KEY = "ot:chartdesk:tv-import";

type Obj = Record<string, any>;
export type TvBundle = {
  version: 1;
  id: string;
  capturedAt?: string;
  theme?: "dark" | "light";
  chartproperties?: Obj;
  mainSeriesProperties?: Obj;
  /** TradingView settings store entries (values as TV keeps them: JSON strings). */
  settings?: Record<string, string>;
  watchlists?: { name: string; color?: string | null; symbols: string[] }[];
  layouts?: { name: string; content: Obj }[];
};

export type ImportReport = { applied: string[]; skipped: string[] };

/* ── colours / enums ─────────────────────────────────────────────────── */

const TV_TO_DIALOG_STYLE: Record<number, number> = { 0: 0, 1: 2, 2: 1 };
const TV_TO_LINESTYLE: Record<number, "solid" | "dotted" | "dashed"> = { 0: "solid", 1: "dotted", 2: "dashed" };

function rgba(c: string): [number, number, number, number] | null {
  const s = c.trim().toLowerCase();
  const hex = /^#([0-9a-f]{3,8})$/.exec(s);
  if (hex) {
    let h = hex[1];
    if (h.length <= 4) h = [...h].map((x) => x + x).join("");
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }
  const fn = /^rgba?\(([^)]*)\)$/.exec(s);
  if (!fn) return null;
  const p = fn[1].split(",").map((x) => parseFloat(x));
  return p.length >= 3 && p.slice(0, 3).every(Number.isFinite) ? [p[0], p[1], p[2], p.length > 3 && Number.isFinite(p[3]) ? p[3] : 1] : null;
}
const hex2 = (n: number) => Math.round(n).toString(16).padStart(2, "0");
/** Opaque #rrggbb of a colour (null when unparseable). */
export function opaque(c: string): string | null {
  const v = rgba(c);
  return v ? `#${hex2(v[0])}${hex2(v[1])}${hex2(v[2])}` : null;
}
/** Alpha of a colour as a TV transparency (0 = opaque, 100 = invisible). */
export function transparencyOf(c: string): number {
  const v = rgba(c);
  return v ? Math.round((1 - v[3]) * 100) : 0;
}
const parse = (v: unknown): any => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};

/* ── chart settings (the Settings dialog draft) ──────────────────────── */

const TITLE_SOURCE: Record<string, string> = { description: "Name", ticker: "Symbol", "ticker-and-description": "Symbol and name" };
const VISIBILITY: Record<string, string> = { alwaysOn: "Always visible", alwaysOff: "Always invisible", visibleOnMouseOver: "Visible on mouse over" };

/** TradingView chart properties -> a chart Settings draft over the dark (or light) factory draft. */
export function draftFromTv(cp: Obj, msp: Obj = {}, theme: "dark" | "light" = "dark", extra: Record<string, unknown> = {}): Draft {
  const d = makeDefaultDraft(theme);
  const row = (k: string) => d[k];
  const check = (k: string, v: unknown) => {
    if (row(k) && typeof v === "boolean") row(k).checked = v;
  };
  const ctrl = (k: string, i: number): CtrlValue | undefined => row(k)?.controls[i];
  const color = (k: string, i: number, v: unknown, style?: unknown, width?: unknown) => {
    const c = ctrl(k, i);
    if (c?.kind !== "color") return;
    if (typeof v === "string" && v) c.color = v;
    if (typeof style === "number" && c.style !== undefined) c.style = TV_TO_DIALOG_STYLE[style] ?? c.style;
    if (typeof width === "number" && c.width !== undefined) c.width = width;
  };
  const select = (k: string, i: number, v: unknown) => {
    const c = ctrl(k, i);
    if (c?.kind === "select" && typeof v === "string") c.value = v;
  };
  const input = (k: string, i: number, v: unknown) => {
    const c = ctrl(k, i);
    if (c?.kind === "input" && (typeof v === "number" || typeof v === "string")) c.value = String(v);
  };
  const multi = (k: string, on: Record<string, unknown>) => {
    const c = ctrl(k, 0);
    if (c?.kind !== "multicheck") return;
    const set = new Set(c.on);
    for (const [name, v] of Object.entries(on)) if (typeof v === "boolean") (v ? set.add(name) : set.delete(name));
    c.on = [...set];
  };
  const pair = (k: string, i: number, up: unknown, down: unknown) => {
    const c = ctrl(k, i);
    if (c?.kind !== "colorPair") return;
    if (typeof up === "string" && up) c.up = up;
    if (typeof down === "string" && down) c.down = down;
  };

  const pp: Obj = cp.paneProperties ?? {};
  const sp: Obj = cp.scalesProperties ?? {};
  // Canvas
  const gradient = pp.backgroundType === "gradient";
  select("canvas:Background", 0, gradient ? "Gradient" : "Solid");
  color("canvas:Background", 1, gradient ? pp.backgroundGradientStartColor : pp.background);
  color("canvas:Background", 2, gradient ? pp.backgroundGradientEndColor : pp.background);
  const mode: string | undefined = pp.gridLinesMode;
  const vert = pp.vertGridProperties ?? {};
  const horz = pp.horzGridProperties ?? {};
  check("canvas:Vertical grid lines", mode ? mode === "both" || mode === "vert" : vert.visible ?? true);
  check("canvas:Horizontal grid lines", mode ? mode === "both" || mode === "horz" : horz.visible ?? true);
  color("canvas:Vertical grid lines", 0, vert.color, vert.style);
  color("canvas:Horizontal grid lines", 0, horz.color, horz.style);
  const ch = pp.crossHairProperties ?? {};
  color("canvas:Crosshair", 0, ch.color, ch.style, ch.width);
  color("canvas:Text", 0, sp.textColor);
  if (sp.fontSize != null) select("canvas:Text", 1, String(sp.fontSize));
  color("canvas:Lines", 0, sp.lineColor);
  input("canvas:Top", 0, pp.topMargin);
  input("canvas:Bottom", 0, pp.bottomMargin);
  if (typeof pp.rightOffset === "number") input("canvas:Right", 0, pp.rightOffset);
  const wm = parse(extra.symbolWatermark) as Obj | undefined;
  if (wm && typeof wm === "object") {
    multi("canvas:Watermark", { Ticker: !!wm.visibility, Interval: !!wm.visibility });
    color("canvas:Watermark", 1, wm.color);
  }
  if (typeof extra.navButtons === "string") select("canvas:Navigation", 0, VISIBILITY[extra.navButtons]);
  if (typeof extra.paneButtons === "string") select("canvas:Pane", 0, VISIBILITY[extra.paneButtons]);
  // Status line
  const lp = pp.legendProperties ?? {};
  check("legend:Logo", lp.showLogo);
  check("legend:Title", lp.showSeriesTitle);
  select("legend:Title", 0, TITLE_SOURCE[msp.statusViewStyle?.symbolTextSource ?? ""]);
  check("legend:Chart values", lp.showSeriesOHLC);
  check("legend:Bar change values", lp.showBarChange);
  check("legend:Volume", lp.showVolume);
  check("legend:Last day change values", lp.showLastDayChange);
  check("legend:Buy/sell buttons", lp.showTradingButtons);
  check("legend:Titles", lp.showStudyTitles);
  check("legend:Inputs", lp.showStudyArguments);
  check("legend:Values", lp.showStudyValues);
  check("legend:Background", lp.showBackground);
  const bg = ctrl("legend:Background", 0);
  if (bg?.kind === "slider" && typeof lp.backgroundTransparency === "number") bg.value = lp.backgroundTransparency;
  // Scales and lines
  check("scales:Scale price chart only", sp.scaleSeriesOnly);
  check("scales:Countdown to bar close", msp.showCountdown);
  check("scales:Save chart left edge position when changing interval", sp.saveLeftEdge);
  multi("scales:Symbol", { Name: sp.showSymbolLabels, Value: sp.showSeriesLastValue, Line: msp.showPriceLine });
  color("scales:Symbol", 1, msp.priceLineColor || undefined, undefined, msp.priceLineWidth);
  if (typeof sp.seriesLastValueMode === "number")
    select("scales:Symbol value mode", 0, sp.seriesLastValueMode === 0 ? "Price and percentage value" : "Value according to scale");
  multi("scales:Previous day close", { Value: sp.showSeriesPrevCloseValue, Line: msp.showPrevClosePriceLine });
  color("scales:Previous day close", 1, msp.prevClosePriceLineColor, undefined, msp.prevClosePriceLineWidth);
  multi("scales:Indicators and financials", { Name: sp.showStudyPlotLabels, Value: sp.showStudyLastValue });
  const hl = msp.highLowAvgPrice ?? {};
  multi("scales:High and low", { Value: hl.highLowPriceLabelsVisible, Line: hl.highLowPriceLinesVisible });
  if (hl.highLowPriceLinesColor) color("scales:High and low", 1, hl.highLowPriceLinesColor, undefined, hl.highLowPriceLinesWidth);
  const ba = msp.bidAsk ?? {};
  multi("scales:Bid and ask", { Value: sp.showBidAskLabels, Line: ba.visible });
  color("scales:Bid and ask", 1, ba.bidLineColor);
  color("scales:Bid and ask", 2, ba.askLineColor);
  const pm = msp.prePostMarket ?? {};
  multi("scales:Pre/post/night market", { Value: sp.showPrePostMarketPriceLabel, Line: pm.visible });
  color("scales:Pre/post/night market", 1, pm.preMarketColor);
  color("scales:Pre/post/night market", 2, pm.postMarketColor);
  color("scales:Pre/post/night market", 3, pm.overnightMarketColor);
  // Symbol
  check("symbol:Adjust data for dividends", msp.dividendsAdjustment);
  if (msp.sessionId) select("symbol:Session", 0, msp.sessionId === "extended" ? "Extended" : "Regular");
  const tz = timezoneOf(cp.timezone);
  if (tz) select("symbol:Timezone", 0, tz.label);
  // Events
  check("events:Dividends", msp.esdShowDividends);
  check("events:Splits", msp.esdShowSplits);
  check("events:Earnings", msp.esdShowEarnings);
  check("events:Earnings breaks", msp.esdShowBreaks);
  check("events:Latest news", msp.showLastNews);
  const br = cp.chartEventsSourceProperties?.breaks ?? {};
  check("events:Session breaks", br.visible);
  color("events:Session breaks", 0, br.color, br.style, br.width);
  // Series styles
  const candleLike = (tab: string, s: Obj | undefined) => {
    if (!s) return;
    check(`${tab}:Color bars based on previous close`, s.barColorsOnPrevClose);
    check(`${tab}:Body`, s.drawBody);
    pair(`${tab}:Body`, 0, s.upColor, s.downColor);
    check(`${tab}:Borders`, s.drawBorder);
    pair(`${tab}:Borders`, 0, s.borderUpColor, s.borderDownColor);
    check(`${tab}:Wick`, s.drawWick);
    pair(`${tab}:Wick`, 0, s.wickUpColor, s.wickDownColor);
  };
  candleLike("style.candle", msp.candleStyle);
  candleLike("style.hollowCandle", msp.hollowCandleStyle);
  candleLike("style.ha", msp.haStyle);
  candleLike("style.volCandles", msp.volCandlesStyle);
  const bar = msp.barStyle;
  if (bar) {
    check("style.bar:Color bars based on previous close", bar.barColorsOnPrevClose);
    check("style.bar:HLC bars", bar.dontDrawOpen);
    color("style.bar:Up color", 0, bar.upColor);
    color("style.bar:Down color", 0, bar.downColor);
    check("style.bar:Thin bars", bar.thinBars);
  }
  for (const [tab, key] of [["style.line", "lineStyle"], ["style.lineWithMarkers", "lineWithMarkersStyle"], ["style.stepline", "steplineStyle"]] as const) {
    const s = msp[key];
    const c = ctrl(`${tab}:Line`, 0);
    if (!s || c?.kind !== "lineColor") continue;
    c.type = s.colorType === "gradient" ? "Gradient" : "Solid";
    if (s.color) c.color = s.color;
    if (s.gradientStartColor) c.start = s.gradientStartColor;
    if (s.gradientEndColor) c.end = s.gradientEndColor;
    if (typeof s.linewidth === "number") c.width = s.linewidth;
    if (typeof s.linestyle === "number") c.style = TV_TO_DIALOG_STYLE[s.linestyle] ?? c.style;
  }
  const area = msp.areaStyle;
  if (area) {
    color("style.area:Line", 0, area.linecolor, area.linestyle, area.linewidth);
    pair("style.area:Fill", 0, area.color1, area.color2);
  }
  const base = msp.baselineStyle;
  if (base) {
    color("style.baseline:Top line", 0, base.topLineColor, base.topLineStyle, base.topLineWidth);
    color("style.baseline:Bottom line", 0, base.bottomLineColor, base.bottomLineStyle, base.bottomLineWidth);
    pair("style.baseline:Fill top area", 0, base.topFillColor1, base.topFillColor2);
    pair("style.baseline:Fill bottom area", 0, base.bottomFillColor1, base.bottomFillColor2);
    input("style.baseline:Base level", 0, base.baseLevelPercentage);
  }
  return d;
}

export function timezoneOf(iana: unknown): { label: string; iana: string } | null {
  if (typeof iana !== "string" || !iana) return null;
  if (iana === "exchange") return { label: "Exchange", iana: "America/New_York" };
  const z = iana === "Etc/UTC" ? "UTC" : iana;
  const t = TIMEZONES.find((x) => x.iana === z);
  return t ? { label: t.label, iana: t.iana } : null;
}

const TV_STYLE_TO_CHART: Record<number, ChartTypeId> = {
  0: "bar", 1: "candle", 2: "line", 3: "area", 4: "renko", 5: "kagi", 6: "pnf", 7: "pb", 8: "ha", 9: "hollowCandle",
  10: "baseline", 11: "range", 12: "hilo", 13: "column", 14: "lineWithMarkers", 15: "stepline", 16: "hlcArea", 19: "volCandles",
};
export const chartTypeOfTv = (style: unknown): ChartTypeId => TV_STYLE_TO_CHART[Number(style)] ?? "candle";

/* ── drawing tool defaults ───────────────────────────────────────────── */

const TOOL_KIND: Record<string, string> = {
  linetooltrendline: "trend-line", linetoolray: "ray", linetoolextended: "extended-line", linetoolinfoline: "info-line",
  linetooltrendangle: "trend-angle", linetoolhorzline: "horizontal-line", linetoolhorzray: "horizontal-ray",
  linetoolvertline: "vertical-line", linetoolcrossline: "cross-line", linetoolarrow: "arrow", linetoolrectangle: "rectangle",
  linetoolcircle: "circle", linetoolellipse: "ellipse", linetooltriangle: "triangle", linetoolrotatedrectangle: "rotated-rectangle",
  linetoolparallelchannel: "parallel-channel", linetooldisjointangle: "disjoint-channel", linetoolflatbottom: "flat-top-bottom",
  linetoolfibretracement: "fib-retracement", linetooltrendbasedfibextension: "trend-based-fib-extension", linetoolfibchannel: "fib-channel",
  linetoolfibspiral: "fib-spiral", linetoolfibspeedresistancearcs: "fib-speed-resistance-arcs", linetoolfibspeedresistancefan: "fib-speed-resistance-fan",
  linetoolfibtimezone: "fib-time-zone", linetoolfibcircles: "fib-circles", linetoolfibwedge: "fib-wedge",
  linetoolpricelabel: "price-label", linetoolpricenote: "price-note", linetoolarrowmarker: "arrow-marker", linetoolregressiontrend: "regression-trend",
  linetoolpricerange: "price-range", linetooldaterange: "date-range", linetooldateandpricerange: "date-and-price-range",
  linetoolriskrewardlong: "long-position", linetoolriskrewardshort: "short-position",
  linetoolabcd: "abcd-pattern", linetool5pointspattern: "xabcd-pattern", linetoolcypherpattern: "cypher-pattern",
  linetoolheadandshoulders: "head-and-shoulders", linetooltrianglepattern: "triangle-pattern", linetoolthreedrivers: "three-drives-pattern",
  linetoolelliottimpulse: "elliott-impulse", linetoolelliottcorrection: "elliott-correction", linetoolelliotttriangle: "elliott-triangle",
  linetoolelliottdoublecombo: "elliott-double-combo", linetoolelliotttriplecombo: "elliott-triple-combo",
  linetoolbarspattern: "bar-pattern", linetoolghostfeed: "ghost-feed", linetoolicon: "font-icon",
  linetooltext: "text", linetoolnote: "note", linetoolcallout: "callout", linetoolcomment: "comment", linetoolsignpost: "signpost",
  linetoolbrush: "brush", linetoolhighlighter: "highlighter", linetoolpath: "path", linetoolpolyline: "polyline",
  linetoolarc: "arc", linetoolbezierquadro: "curve", linetoolbeziercubic: "double-curve",
};

/** TV `linetool*` property bag -> the drawing core's DrawingStyle fields (only the ones TV set). */
export function drawingStyleFromTv(kind: string, p: Obj): Obj {
  const s: Obj = {};
  // Main stroke colour: line tools use linecolor; shapes/text use color (rectangle: its border).
  const stroke = p.linecolor ?? p.lineColor ?? p.color;
  if (typeof stroke === "string") s.color = stroke;
  if (kind === "text" || kind === "callout" || kind === "comment" || kind === "note" || kind === "price-label") {
    if (typeof p.color === "string") s.textColor = p.color;
    if (typeof p.bordercolor === "string" || typeof p.borderColor === "string") s.borderColor = p.bordercolor ?? p.borderColor;
    if (typeof p.borderColor === "string" || typeof p.bordercolor === "string") s.color = p.bordercolor ?? p.borderColor;
  }
  const width = p.linewidth ?? p.lineWidth;
  if (typeof width === "number") s.width = width;
  const ls = p.linestyle ?? p.lineStyle;
  if (typeof ls === "number" && TV_TO_LINESTYLE[ls]) s.lineStyle = TV_TO_LINESTYLE[ls];
  const tc = p.textcolor ?? p.textColor;
  if (typeof tc === "string") s.textColor = tc;
  const fs = p.fontsize ?? p.fontSize;
  if (typeof fs === "number") s.fontSize = fs;
  for (const k of ["bold", "italic", "extendLeft", "extendRight", "showMiddlePoint", "showPriceRange", "showBarsRange", "showDateTimeRange",
    "showDistance", "showAngle", "showPercentPriceRange", "showPipsPriceRange", "showCoeffs", "showPrices", "showLabels", "showText",
    "showWave", "drawBorder", "wordWrap", "fullCircles", "counterclockwise", "extendLines", "showPriceLabels", "fillLabelBackground", "showTime"] as const)
    if (typeof p[k] === "boolean") s[k] = p[k];
  for (const k of ["leftEnd", "rightEnd", "wordWrapWidth", "statsPosition"] as const) if (typeof p[k] === "number") s[k] = p[k];
  for (const k of ["horzLabelsAlign", "vertLabelsAlign", "horzTextAlign", "vertTextAlign"] as const) if (typeof p[k] === "string") s[k] = p[k];
  if (typeof p.alwaysShowStats === "boolean") s.showStats = p.alwaysShowStats;
  if (typeof p.showPrice === "boolean") s.showPriceLabels = p.showPrice;
  if (typeof p.labelBackgroundColor === "string") s.labelBackgroundColor = p.labelBackgroundColor;
  if (typeof p.fillBackground === "boolean") s.fillBackground = p.fillBackground;
  if (typeof p.backgroundColor === "string") {
    const a = rgba(p.backgroundColor);
    if (a && a[3] < 1) {
      // The fill's alpha is its transparency; TV's own `transparency` field is legacy once the colour has one.
      s.backgroundColor = opaque(p.backgroundColor);
      s.transparency = transparencyOf(p.backgroundColor);
    } else {
      s.backgroundColor = p.backgroundColor;
      if (typeof p.transparency === "number") s.transparency = Math.round(p.transparency);
    }
  } else if (typeof p.transparency === "number") s.transparency = Math.round(p.transparency);
  // Fib-type level ladders: level1..levelN.
  const levels: Obj[] = [];
  for (let i = 1; i <= 30; i++) {
    const l = p[`level${i}`];
    if (!l || typeof l !== "object" || typeof l.coeff !== "number") continue;
    const def: Obj = { coeff: l.coeff, color: l.color ?? s.color ?? "#787B86", visible: l.visible !== false };
    if (typeof l.linewidth === "number") def.width = l.linewidth;
    if (typeof l.linestyle === "number" && TV_TO_LINESTYLE[l.linestyle]) def.style = TV_TO_LINESTYLE[l.linestyle];
    levels.push(def);
  }
  if (levels.length) s.levels = levels;
  return s;
}

export function drawingDefaultsFromTv(settings: Record<string, string>, report?: ImportReport): Record<string, Obj> {
  const out: Record<string, Obj> = {};
  for (const [key, raw] of Object.entries(settings)) {
    if (!key.startsWith("linetool") || key.includes(".")) continue;
    const kind = TOOL_KIND[key];
    const p = parse(raw);
    if (!kind || !p || typeof p !== "object") {
      if (!kind) report?.skipped.push(`drawing tool ${key}: no matching tool`);
      continue;
    }
    const s = drawingStyleFromTv(kind, p);
    if (Object.keys(s).length) out[kind] = s;
  }
  return out;
}

/* ── indicator defaults ──────────────────────────────────────────────── */

type StudyMap = { id: string; inputs: (version: number) => (string | null)[]; plots?: Record<string, string> };
const MA_RIBBON_INPUTS = [1, 2, 3, 4].flatMap((n) => [`showMa${n}`, `ma${n}Type`, `ma${n}Source`, `ma${n}Length`, `ma${n}Color`]);
/** TradingView built-in scripts we map, keyed by the script id inside the TV study key. */
const STUDIES: Record<string, StudyMap> = {
  "STD;Bollinger_Bands": { id: "bb", inputs: () => ["length", "maType", "src", "mult", "offset"] },
  "STD;SMA": { id: "sma", inputs: () => ["len", "src", "offset", "maType", "maLength", "bbMult"] },
  "STD;EMA": { id: "ema", inputs: () => ["length", "src", "offset", "maType", "maLength", "bbMult"] },
  // RSI: v35 added the divergence input after the MA inputs.
  "STD;RSI": { id: "rsi", inputs: (v) => (v >= 35 ? ["length", "src", "maType", "maLength", "bbMult", "calculateDivergence"] : ["length", "src", "maType", "maLength", "bbMult"]) },
  "STD;VWAP": { id: "vwap", inputs: () => ["hideOnDWM", "anchor", "src", "offset", "calcMode", "showBand1", "bandMult1", "showBand2", "bandMult2", "showBand3", "bandMult3"] },
  "STD;MA%Ribbon": { id: "ma-ribbon", inputs: () => MA_RIBBON_INPUTS },
};

/** "Script$STD;RSI@tv-scripting_35_0" / "Script$STD;RSI@tv-scripting-101[v.35.0]" -> ["STD;RSI", 35]. */
export function tvScriptOf(key: string): [string, number] | null {
  const m = /Script\$([^@]+)@tv-scripting(?:_(\d+)_\d+|-\d+\[v\.(\d+)\.\d+\])/.exec(key);
  return m ? [m[1], Number(m[2] ?? m[3])] : null;
}

/** One TV study state (inputs in_N + styles plot_N) -> the pane's indicator settings for its registry entry. */
export function studySettingsFromTv(map: StudyMap, version: number, state: Obj): Obj {
  const ids = map.inputs(version);
  const inputs: Obj = {};
  for (const [k, v] of Object.entries(state.inputs ?? {})) {
    const m = /^in_(\d+)$/.exec(k);
    const id = m ? ids[Number(m[1])] : null;
    if (id && (typeof v === "number" || typeof v === "string" || typeof v === "boolean")) inputs[id] = v;
  }
  const styles: Obj = {};
  for (const [k, st] of Object.entries(state.styles ?? {}) as [string, Obj][]) {
    const m = /^plot_(\d+)$/.exec(k);
    if (!m || !st) continue;
    const o: Obj = {};
    if (typeof st.color === "string") o.color = st.color;
    if (typeof st.linewidth === "number") o.lineWidth = st.linewidth;
    // TV `display`: 0 hidden, anything else shown somewhere.
    if (typeof st.display === "number") o.visible = st.display !== 0;
    if (Object.keys(o).length) styles[`plot${m[1]}`] = o;
  }
  return { inputs, styles, options: defaultIndicatorOptions() };
}

/** TV Volume study state -> the app's Volume settings (palette + MA). */
export function volumeSettingsFromTv(state: Obj): Obj {
  const pal = state.palettes?.volumePalette?.colors ?? {};
  const palette = [pal["0"]?.color, pal["1"]?.color].filter((c): c is string => typeof c === "string");
  const inputs: Obj = {};
  const inp = state.inputs ?? {};
  if (typeof inp.length === "number") inputs.maLength = inp.length;
  if (typeof inp.col_prev_close === "boolean") inputs.colorOnPrevClose = inp.col_prev_close;
  const styles: Obj = {};
  if (palette.length === 2) styles.vol = { palette };
  const ma = state.styles?.["vol_ma"];
  if (ma) styles.vol_ma = { ...(typeof ma.color === "string" ? { color: ma.color } : {}), ...(typeof ma.display === "number" ? { visible: ma.display !== 0 } : {}) };
  return { inputs, styles, options: defaultIndicatorOptions() };
}

export function indicatorDefaultsFromTv(settings: Record<string, string>, report?: ImportReport): Record<string, Obj> {
  const best: Record<string, [number, Obj]> = {};
  for (const [key, raw] of Object.entries(settings)) {
    if (!key.startsWith("study_")) continue;
    const state = parse(raw);
    if (!state || typeof state !== "object") continue;
    if (key === "study_Volume@tv-basicstudies") {
      best.volume = [Infinity, volumeSettingsFromTv(state)];
      continue;
    }
    const sv = tvScriptOf(key);
    const map = sv && STUDIES[sv[0]];
    if (!sv || !map) {
      report?.skipped.push(`indicator default ${key.slice(6, 70)}: not mapped`);
      continue;
    }
    // TV keeps one default per script version; the newest wins.
    if (!best[map.id] || best[map.id][0] < sv[1]) best[map.id] = [sv[1], studySettingsFromTv(map, sv[1], state)];
  }
  return Object.fromEntries(Object.entries(best).map(([id, [, s]]) => [id, s]));
}

/* ── watchlists ──────────────────────────────────────────────────────── */

const FLAGS = new Set(["red", "blue", "green", "orange", "purple", "cyan", "pink", "yellow"]);
const row = (tv: string) => {
  const ticker = tvSymbol(tv);
  return { ticker, short: ticker.slice(ticker.indexOf(":") + 1), last: "—", changePercent: "—", prePostChange: "" };
};
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "list";

/** TV lists -> watchlist store lists. `###Section` entries open a section. */
export function watchlistsFromTv(lists: NonNullable<TvBundle["watchlists"]>): Obj[] {
  return lists
    .filter((l) => l.symbols?.length)
    .map((l) => {
      const groups: { name: string; rows: Obj[] }[] = [];
      const extras: Obj[] = [];
      for (const s of l.symbols) {
        if (s.startsWith("###")) groups.push({ name: s.slice(3), rows: [] });
        else (groups.length ? groups[groups.length - 1].rows : extras).push(row(s));
      }
      if (!groups.length) groups.push({ name: l.name.toUpperCase(), rows: extras.splice(0) });
      const flag = l.color && FLAGS.has(l.color) ? l.color : null;
      return { id: `tv-${slug(l.name)}`, name: l.name, flag, emoji: null, groups, extras, favorite: false, sort: "default" };
    });
}

/* ── layouts ─────────────────────────────────────────────────────────── */

const PANE_ID = () => `p${Math.random().toString(36).slice(2, 10)}`;

/** One TV layout -> a saved layout snapshot (single chart; the first TV chart of the layout). */
export function layoutFromTv(name: string, content: Obj, settingsDraft: Draft, report?: ImportReport): Obj | null {
  const ch = content?.charts?.[0];
  if (!ch) return null;
  let main: Obj | null = null;
  const indicators: string[] = [];
  const indicatorSettings: Obj = {};
  for (const pane of ch.panes ?? [])
    for (const src of pane.sources ?? []) {
      const st = src.state ?? {};
      if (src.type === "MainSeries") main = st;
      else if (typeof src.type === "string" && src.type.startsWith("study_Volume")) {
        indicators.push("volume");
        indicatorSettings.volume = volumeSettingsFromTv(st);
      } else if (src.type === "Study") {
        const sv = tvScriptOf(String(src.metaInfo?.id ?? src.metaInfo ?? st.id ?? ""));
        const map = sv && STUDIES[sv[0]];
        if (!sv || !map) {
          report?.skipped.push(`layout "${name}": indicator ${st.shortTitle ?? st.title ?? src.metaInfo?.description ?? sv?.[0] ?? "?"} not available`);
          continue;
        }
        let id = map.id;
        for (let n = 2; indicators.includes(id); n++) id = `${map.id}#${n}`;
        indicators.push(id);
        indicatorSettings[id] = studySettingsFromTv(map, sv[1], st);
      }
    }
  if (!main) return null;
  // The layout's own chart properties win; the account-wide ones fill what it lacks.
  const draft = ch.chartProperties ? draftFromTv(ch.chartProperties, main, "dark") : cloneDraft(settingsDraft);
  const pane = {
    id: PANE_ID(),
    symbol: tvSymbol(String(main.symbol ?? "NASDAQ:AAPL")),
    interval: String(main.interval ?? "1D"),
    chartType: chartTypeOfTv(main.style),
    session: main.sessionId === "extended" ? "ETH" : "RTH",
    indicators,
    indicatorSettings,
    settings: draft,
    settingsFp: SETTINGS_FINGERPRINT,
    settingsRev: SETTINGS_REV,
  };
  return { id: `tv-${slug(name)}-${Date.now().toString(36)}`, name, snapshot: { layout: "s", activePane: 0, panes: [pane] }, updatedAt: Date.now() };
}

/* ── apply ───────────────────────────────────────────────────────────── */

const readJson = (k: string): any => {
  try {
    const v = kv.getItem(k);
    return v ? JSON.parse(v) : undefined;
  } catch {
    return undefined;
  }
};

/** Write the bundle into the stores. Returns what was applied and what was skipped. */
export function applyTvBundle(b: TvBundle): ImportReport {
  const report: ImportReport = { applied: [], skipped: [] };
  const s = b.settings ?? {};
  const cp = b.chartproperties ?? parse(s.chartproperties);
  const msp = b.mainSeriesProperties ?? parse(s["chartproperties.mainSeriesProperties"]);
  const theme = b.theme === "light" ? "light" : "dark";
  kv.setItem("ot:theme", theme);
  report.applied.push(`theme ${theme}`);
  let draft: Draft | undefined;
  if (cp && typeof cp === "object") {
    draft = draftFromTv(cp, msp ?? {}, theme, { symbolWatermark: s.symbolWatermark });
    kv.setItem("ot:chart-settings-defaults", JSON.stringify({ fingerprint: SETTINGS_FINGERPRINT, rev: SETTINGS_REV, draft: cloneDraft(draft) }));
    const templates = (readJson("ot:chart-settings-templates") as Obj[] | undefined)?.filter((t) => t?.name !== "TradingView") ?? [];
    templates.push({ name: "TradingView", fingerprint: SETTINGS_FINGERPRINT, rev: SETTINGS_REV, draft: cloneDraft(draft) });
    kv.setItem("ot:chart-settings-templates", JSON.stringify(templates));
    report.applied.push("chart settings (defaults + template \"TradingView\")");
    const tz = timezoneOf(cp.timezone);
    if (tz) {
      kv.setItem("ot:timezone", JSON.stringify(tz));
      report.applied.push(`timezone ${tz.label}`);
    }
  }
  const drawings = drawingDefaultsFromTv(s, report);
  if (Object.keys(drawings).length) {
    kv.setItem("ot:drawing-kind-defaults", JSON.stringify({ ...(readJson("ot:drawing-kind-defaults") ?? {}), ...drawings }));
    report.applied.push(`drawing defaults for ${Object.keys(drawings).length} tools`);
  }
  const studies = indicatorDefaultsFromTv(s, report);
  if (Object.keys(studies).length) {
    kv.setItem("ot:indicator-defaults", JSON.stringify({ ...(readJson("ot:indicator-defaults") ?? {}), ...studies }));
    report.applied.push(`indicator defaults: ${Object.keys(studies).join(", ")}`);
  }
  const drawPrefs = parse(s.drawings);
  if (drawPrefs && typeof drawPrefs === "object") {
    // TV magnetMode 0 weak / 1 strong (the on/off switch itself is per session here).
    if (typeof drawPrefs.magnetMode === "number") kv.setItem("ot:magnet-mode", drawPrefs.magnetMode === 1 ? "strong" : "weak");
    if (typeof drawPrefs.magnetSnapsToIndicators === "boolean") kv.setItem("ot:magnet-snap-indicators", String(drawPrefs.magnetSnapsToIndicators));
  }
  if (b.watchlists?.length) {
    const lists = watchlistsFromTv(b.watchlists);
    const cur = readJson("ot:watchlist:lists:v1");
    const keep: Obj[] = Array.isArray(cur?.lists) ? cur.lists.filter((l: Obj) => !String(l.id).startsWith("tv-")) : [];
    const all = [...lists, ...keep];
    if (lists.length) {
      kv.setItem("ot:watchlist:lists:v1", JSON.stringify({ lists: all, activeId: lists[0].id, alerts: cur?.alerts ?? {}, alertMeta: cur?.alertMeta ?? {}, v: 2 }));
      report.applied.push(`watchlists: ${lists.map((l) => `${l.name} (${l.groups.reduce((n: number, g: Obj) => n + g.rows.length, 0)})`).join(", ")}`);
    }
  }
  if (b.layouts?.length && draft) {
    const made = b.layouts.map((l) => layoutFromTv(l.name, l.content, draft!, report)).filter((x): x is Obj => !!x);
    if (made.length) {
      const cur: Obj[] = (readJson("ot:layouts") ?? []).filter((l: Obj) => !String(l.id).startsWith("tv-"));
      kv.setItem("ot:layouts", JSON.stringify([...made, ...cur]));
      report.applied.push(`layouts: ${made.map((l) => l.name).join(", ")}`);
    }
  }
  return report;
}

/** Boot hook: fetch the bundle (if the server has one) and apply it once per bundle id. */
export async function maybeImportTradingView(): Promise<ImportReport | null> {
  // Names saved by an earlier import (OANDA:XAUUSD, FRED:IORB...) are rewritten to ours every start (idempotent).
  const migrated = migrateTvSymbols();
  if (migrated) console.info(`[chartdesk] ${migrated} TradingView symbol names rewritten`);
  let b: TvBundle;
  try {
    const r = await fetch(BUNDLE_URL, { cache: "no-store" });
    if (!r.ok) return null;
    b = (await r.json()) as TvBundle;
  } catch {
    return null;
  }
  if (!b || b.version !== 1 || !b.id || kv.getItem(DONE_KEY) === JSON.stringify(b.id)) return null;
  const report = applyTvBundle(b);
  kv.setItem(DONE_KEY, JSON.stringify(b.id));
  console.info("[chartdesk] TradingView import", report);
  return report;
}
