"""Alerts while the app is closed.

The app sends chartd the rules chartd can evaluate on its own (`POST /chartdesk/v1/alerts`): the price crossing,
above or below a fixed value or a drawn line (the app resolves a line to a level at a time and a slope). While the
app is open it sends a heartbeat every 20 s and does the alerting itself; a minute without one and chartd takes
over: every 15 s it reads the symbols' last prices, fires what the rules say, shows a desktop notification and
keeps the fire for the app (`GET /chartdesk/v1/alerts/fired?since=<ms>`), which logs it and stops a fired
"only once" rule when it starts again. Indicator and multi-condition alerts need the app open.

Rules, fires and each rule's last price are kept in <data>/alerts.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import math
import time
from pathlib import Path
from typing import Awaitable, Callable

CHECK_EVERY = 15.0
APP_GONE_AFTER = 60.0
OPS = ("crossing", "crossing_up", "crossing_down", "greater", "less")
#: Seconds of a rule's bar, for "once per bar" (the app's interval ids: "1", "60", "1D", "1W"...).
def period_of(resolution: str) -> int:
    r = (resolution or "1D").upper()
    try:
        if r.endswith("D"):
            return 86400 * int(r[:-1] or 1)
        if r.endswith("W"):
            return 7 * 86400 * int(r[:-1] or 1)
        if r.endswith("M"):
            return 30 * 86400 * int(r[:-1] or 1)
        if r.endswith("S"):
            return int(r[:-1] or 1)
        return 60 * int(r)
    except ValueError:
        return 86400


def level_of(rule: dict, now: float) -> float | None:
    lv = rule.get("level") or {}
    if "v" in lv:
        return float(lv["v"])
    if "p0" in lv:
        return float(lv["p0"]) + float(lv.get("slope", 0.0)) * (now - float(lv.get("t0", now)))
    return None


def hit(op: str, prev: float | None, price: float, level: float) -> bool:
    if op == "greater":
        return price > level
    if op == "less":
        return price < level
    if prev is None:
        return False
    up = prev <= level < price
    down = prev >= level > price
    return {"crossing": up or down, "crossing_up": up, "crossing_down": down}.get(op, False)


class Alerts:
    def __init__(self, dir: Path, prices: Callable[[list[str]], Awaitable[dict[str, float]]],
                 notify: Callable[[str, str], Awaitable[None]]) -> None:
        self.path = dir / "alerts.json"
        self.prices, self.notify = prices, notify
        self.rules: list[dict] = []
        self.fired: list[dict] = []
        self.prev: dict[str, float] = {}
        self.last_fire: dict[str, float] = {}
        self.last_seen = 0.0
        try:
            d = json.loads(self.path.read_text())
            self.rules, self.fired, self.prev, self.last_fire = d.get("rules", []), d.get("fired", []), d.get("prev", {}), d.get("lastFire", {})
        except Exception:
            pass

    def save(self) -> None:
        self.fired = self.fired[-500:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": self.rules, "fired": self.fired, "prev": self.prev, "lastFire": self.last_fire}))
        tmp.replace(self.path)

    def set_rules(self, rules: list[dict]) -> int:
        keep = [r for r in rules if r.get("op") in OPS and r.get("symbol") and isinstance(r.get("level"), dict)]
        done = {r["id"] for r in self.rules if r.get("done")}
        for r in keep:
            if r["id"] in done and r.get("frequency") == "only_once":
                r["done"] = True  # fired while the app was closed; the app has not stopped it yet
        self.rules = keep
        ids = {r["id"] for r in keep}
        self.prev = {k: v for k, v in self.prev.items() if k in ids}
        self.save()
        return len(keep)

    def heartbeat(self) -> None:
        self.last_seen = time.time()

    def app_open(self) -> bool:
        return time.time() - self.last_seen < APP_GONE_AFTER

    async def run(self) -> None:
        while True:
            await asyncio.sleep(CHECK_EVERY)
            if self.app_open() or not self.rules:
                continue
            try:
                await self.check()
            except Exception as e:  # a failed round is retried on the next
                print(f"[chartd] alerts: {e}", flush=True)

    async def check(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        live = [r for r in self.rules if not r.get("done") and (not r.get("expiresAt") or r["expiresAt"] > now * 1000)]
        if not live:
            return []
        tickers = sorted({ticker_of(r["symbol"]) for r in live})
        prices = await self.prices(tickers)
        out = []
        for r in live:
            price = prices.get(ticker_of(r["symbol"]))
            level = level_of(r, now)
            if price is None or level is None or not math.isfinite(price):
                print(f"[chartd] alert {r['id']}: no price for {r['symbol']}", flush=True)
                continue
            prev = self.prev.get(r["id"])
            self.prev[r["id"]] = price
            if not hit(r["op"], prev, price, level):
                continue
            freq = r.get("frequency", "only_once")
            last = self.last_fire.get(r["id"], 0.0)
            if freq in ("once_per_bar", "once_per_bar_close"):
                p = period_of(r.get("resolution", "1D"))
                if last and int(last // p) == int(now // p):
                    continue
            elif freq == "every_time" and now - last < 60:
                continue
            self.last_fire[r["id"]] = now
            if freq == "only_once":
                r["done"] = True
            f = {"alertId": r["id"], "symbol": r["symbol"], "name": r.get("name") or None, "message": r.get("message") or "",
                 "price": price, "level": level, "op": r["op"], "time": int(now * 1000), "resolution": r.get("resolution", "1D")}
            self.fired.append(f)
            out.append(f)
            title = r.get("name") or f"{ticker_of(r['symbol'])} {r['op'].replace('_', ' ')} {level:g}"
            await self.notify(title, r.get("message") or f"{ticker_of(r['symbol'])} at {price:g}")
        self.save()
        return out

    def fired_since(self, since_ms: int) -> list[dict]:
        return [f for f in self.fired if f["time"] > since_ms]


def ticker_of(symbol: str) -> str:
    return symbol.split(":", 1)[1] if ":" in symbol else symbol


async def desktop_notify(title: str, body: str) -> None:
    """A freedesktop notification (notify-send) on Linux; nothing elsewhere, or with CHARTD_NOTIFY=off (the desktop app
    stays in the tray and notifies from the page itself)."""
    if sys.platform != "linux" or os.environ.get("CHARTD_NOTIFY") == "off":
        return
    try:
        p = await asyncio.create_subprocess_exec(
            "notify-send", "-a", "Chart Desk", "-i", "chartdesk", "-u", "critical", title, body,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(p.wait(), 10)
    except Exception as e:
        print(f"[chartd] notify: {e}", flush=True)
