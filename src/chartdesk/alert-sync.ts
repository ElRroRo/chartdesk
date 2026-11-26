/*
 * Chart Desk: alerts that keep working with the app closed.
 *
 * chartd (the local data server, a systemd user service) evaluates the rules it can on its own: the price crossing,
 * above or below a fixed value or a drawn line. This module sends it those rules whenever they change (a drawn
 * line resolved to its level now and its slope per second, re-sent every five minutes so a sloped line stays
 * anchored), a heartbeat every 20 s while the app is open (chartd then leaves the alerting to the app), and at start
 * reads what chartd fired while the app was closed: each goes into the alert log as an offline event, and a fired
 * "only once" rule is stopped as triggered.
 */
import { createEffect, createRoot, on } from "solid-js";
import { alertStore, type AlertRule } from "../data/alert-store";
import { drawingPriceLevel } from "../data/alert-condition";
import * as kv from "../data/kv";

const CHARTD = "http://127.0.0.1:8798/chartdesk/v1";
const SINCE_KEY = "ot:chartdesk:alerts-fired-since";
const OPS = new Set(["crossing", "crossing_up", "crossing_down", "greater", "less"]);

type Level = { v: number } | { p0: number; t0: number; slope: number };
export type ClosedAppRule = {
  id: string; symbol: string; op: string; level: Level; frequency: string; resolution: string;
  name: string; message: string; expiresAt: number | null;
};

/** The rule as chartd evaluates it, or null when it needs the app (indicators, several conditions, bands...). */
export function closedAppRule(r: AlertRule, nowSec: number): ClosedAppRule | null {
  if (!r.enabled || (r.and?.length ?? 0) > 0 || r.left.kind !== "price" || !OPS.has(r.op)) return null;
  let level: Level;
  if (r.right.kind === "value") {
    level = { v: r.right.value };
  } else if (r.right.kind === "drawing" && !r.right.band && r.right.plot == null) {
    const p0 = drawingPriceLevel(r.symbol, r.right.drawingId, nowSec, r.right.level);
    const p1 = drawingPriceLevel(r.symbol, r.right.drawingId, nowSec + 86400, r.right.level);
    if (p0 == null || !Number.isFinite(p0)) return null;
    level = { p0, t0: nowSec, slope: p1 != null && Number.isFinite(p1) ? (p1 - p0) / 86400 : 0 };
  } else {
    return null;
  }
  return {
    id: r.id, symbol: r.symbol, op: r.op, level, frequency: r.frequency, resolution: r.resolution,
    name: r.name ?? "", message: r.message ?? "", expiresAt: r.expiresAt ?? null,
  };
}

async function post(path: string, body: unknown): Promise<void> {
  await fetch(`${CHARTD}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function sendRules(): void {
  const now = Date.now() / 1000;
  const rules = alertStore.rules().map((r) => closedAppRule(r, now)).filter((r): r is ClosedAppRule => r != null);
  void post("/alerts", { rules }).catch(() => { /* chartd down: sent again on the next change or in five minutes */ });
}

/** What chartd fired while the app was closed -> the alert log (offline events); fired "only once" rules stop. */
async function collectFired(): Promise<void> {
  const since = Number(kv.getItem(SINCE_KEY) ?? 0) || 0;
  const r = await fetch(`${CHARTD}/alerts/fired?since=${since}`);
  if (!r.ok) return;
  const { fired } = (await r.json()) as { fired: { alertId: string; symbol: string; name: string | null; message: string; price: number; time: number; resolution: string }[] };
  let last = since;
  for (const f of fired) {
    const rule = alertStore.rule(f.alertId);
    alertStore.recordFire({
      alertId: f.alertId, symbol: f.symbol, resolution: f.resolution, name: f.name, message: f.message || `${f.symbol} at ${f.price}`,
      fireTime: f.time, barTime: null, soundFile: "", logoUrl: null, offline: true, receivedAt: Date.now(),
    });
    if (rule && rule.frequency === "only_once" && rule.enabled) alertStore.setEnabled(rule.id, false, "triggered");
    last = Math.max(last, f.time);
  }
  kv.setItem(SINCE_KEY, String(last));
}

let started = false;
export function startAlertSync(): void {
  if (started) return;
  started = true;
  void collectFired().catch(() => {}).finally(() => {
    createRoot(() => {
      // Any change to the rules (added, edited, stopped, a drawing moved and the rule saved again) is sent.
      createEffect(on(() => JSON.stringify(alertStore.rules()), () => sendRules()));
    });
  });
  window.setInterval(() => void post("/heartbeat", {}).catch(() => {}), 20_000);
  window.setInterval(sendRules, 5 * 60_000);
}
