import { describe, expect, it, vi } from "vitest";
vi.mock("../../data/alert-condition", () => ({ drawingPriceLevel: (_s: string, _id: string, t: number) => 100 + (t - 1000) * 0.5 }));
import { closedAppRule } from "../alert-sync";
import type { AlertRule } from "../../data/alert-store";

const base = { id: "a", symbol: "NASDAQ:AAPL", resolution: "1D", frequency: "only_once", name: "", message: "", sound: "", popup: true, enabled: true, createdAt: 0, expiresAt: null } as unknown as AlertRule;

describe("rules chartd evaluates while the app is closed", () => {
  it("takes price against a value or a line", () => {
    expect(closedAppRule({ ...base, left: { kind: "price" }, op: "crossing_up", right: { kind: "value", value: 200 } } as AlertRule, 1000)?.level).toEqual({ v: 200 });
    expect(closedAppRule({ ...base, left: { kind: "price" }, op: "less", right: { kind: "drawing", drawingId: "d" } } as AlertRule, 1000)?.level).toEqual({ p0: 100, t0: 1000, slope: 0.5 });
  });
  it("leaves the rest to the app", () => {
    expect(closedAppRule({ ...base, left: { kind: "indicator", indicatorId: "rsi" }, op: "crossing", right: { kind: "value", value: 70 } } as AlertRule, 1000)).toBeNull();
    expect(closedAppRule({ ...base, left: { kind: "price" }, op: "moving_up_pct", right: { kind: "value", value: 5 } } as AlertRule, 1000)).toBeNull();
    expect(closedAppRule({ ...base, enabled: false, left: { kind: "price" }, op: "greater", right: { kind: "value", value: 5 } } as AlertRule, 1000)).toBeNull();
  });
});
