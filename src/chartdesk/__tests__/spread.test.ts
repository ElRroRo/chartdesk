import { describe, expect, it } from "vitest";
import { spreadFormula } from "../spread";

describe("spread formulas typed in symbol search", () => {
  it("takes formulas", () => {
    expect(spreadFormula("xlf/spy")).toBe("XLF/SPY");
    expect(spreadFormula("US10Y - US02Y")).toBe("US10Y-US02Y");
    expect(spreadFormula("(AAPL+MSFT)/2")).toBe("(AAPL+MSFT)/2");
    expect(spreadFormula("2*GLD-SLV")).toBe("2*GLD-SLV");
    expect(spreadFormula("ES1!/NQ1!")).toBe("ES1!/NQ1!");
  });
  it("leaves tickers and junk", () => {
    for (const t of ["AAPL", "BRK.B", "ES1!", "(AAPL", "AAPL)", "XLF//SPY", "SPY/", "2*3", ""]) expect(spreadFormula(t)).toBeNull();
  });
});
