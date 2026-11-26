/*
 * Chart Desk: a spread typed in symbol search (XLF/SPY, US10Y-US02Y, (AAPL+MSFT)/2) is the symbol
 * CALC:<formula>; chartd parses and evaluates it over the legs' bars.
 */

/** The formula when `text` is one (an operator, a ticker, balanced brackets), upper case without spaces; else null. */
export function spreadFormula(text: string): string | null {
  const f = text.replace(/\s+/g, "").toUpperCase();
  if (!f || f.length > 64 || !/[-+*/()]/.test(f) || !/^[A-Z0-9.!+\-*/()]+$/.test(f)) return null;
  let depth = 0;
  for (const c of f) {
    depth += c === "(" ? 1 : c === ")" ? -1 : 0;
    if (depth < 0) return null;
  }
  if (depth !== 0) return null;
  // Operands: tickers (with a letter) or numbers; at least one ticker, and no two operators in a row except a sign.
  const operands = f.split(/[-+*/()]+/).filter(Boolean);
  if (!operands.some((o) => /[A-Z]/.test(o))) return null;
  if (/[+*/]{2,}|[-+*/]$|^[+*/]/.test(f)) return null;
  return f;
}
