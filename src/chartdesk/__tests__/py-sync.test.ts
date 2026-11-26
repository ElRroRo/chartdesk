import { describe, expect, it } from "vitest";
import { pySource } from "../py-sync";

describe("Python indicator scripts", () => {
  it("plots chartd's series over the chart's own symbol", () => {
    const s = pySource({ id: "RSI2", title: 'RSI "2"', overlay: false, error: null }, "/home/u/chartdesk/indicators");
    expect(s).toContain('indicator("Py: RSI \\"2\\"", { overlay: false });');
    expect(s).toContain('request.security("PY:PY.RSI2." + String(syminfo.tickerid).split(":").pop(), "", () => close)');
    expect(s).toContain("/home/u/chartdesk/indicators/rsi2.py");
  });
});
