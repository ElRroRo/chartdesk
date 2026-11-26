"""The bar store: one SQLite file (WAL), bars kept as Yahoo sends them (split-adjusted), plus splits and dividends.

Unadjusted prices are rebuilt on read from the splits. When a new split appears, the daily series is fetched
again whole and the stored intraday bars older than it are rescaled; the recent intraday window is fetched again.
Times are Unix ms. A daily bar's time is midnight of its trading day in the exchange's time zone.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

Row = tuple[int, float, float, float, float, float]  # (t ms, o, h, l, c, v)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  sym TEXT NOT NULL, tf TEXT NOT NULL, t INTEGER NOT NULL,
  o REAL, h REAL, l REAL, c REAL, v REAL,
  PRIMARY KEY (sym, tf, t)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS series (
  sym TEXT NOT NULL, tf TEXT NOT NULL,
  fetched_at REAL NOT NULL,  -- Unix s of the last successful fetch
  PRIMARY KEY (sym, tf)
);
CREATE TABLE IF NOT EXISTS splits (sym TEXT NOT NULL, t INTEGER NOT NULL, num REAL, den REAL, PRIMARY KEY (sym, t));
CREATE TABLE IF NOT EXISTS dividends (sym TEXT NOT NULL, t INTEGER NOT NULL, amount REAL, PRIMARY KEY (sym, t));
CREATE TABLE IF NOT EXISTS meta (sym TEXT PRIMARY KEY, tz TEXT, name TEXT, currency TEXT, exchange TEXT);
"""


@dataclass
class Series:
    fetched_at: float
    first_t: int | None
    last_t: int | None


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    # ── bars ──
    def upsert_bars(self, sym: str, tf: str, rows: list[Row]) -> None:
        with self.lock, self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)", [(sym, tf, *r) for r in rows]
            )

    def bars(self, sym: str, tf: str, t0: int, t1: int) -> list[Row]:
        with self.lock:
            return self.db.execute(
                "SELECT t,o,h,l,c,v FROM bars WHERE sym=? AND tf=? AND t BETWEEN ? AND ? ORDER BY t", (sym, tf, t0, t1)
            ).fetchall()

    def last_bars(self, sym: str, tf: str, n: int) -> list[Row]:
        with self.lock:
            rows = self.db.execute(
                "SELECT t,o,h,l,c,v FROM bars WHERE sym=? AND tf=? ORDER BY t DESC LIMIT ?", (sym, tf, n)
            ).fetchall()
        return rows[::-1]

    def replace_series(self, sym: str, tf: str, rows: list[Row]) -> None:
        """The whole series anew (a daily history fetched again after a split)."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM bars WHERE sym=? AND tf=?", (sym, tf))
            self.db.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)", [(sym, tf, *r) for r in rows])

    def rescale_before(self, sym: str, tf: str, t: int, k: float) -> None:
        """A k-for-1 split at `t`: older prices divide by k, volumes multiply."""
        with self.lock, self.db:
            self.db.execute(
                "UPDATE bars SET o=o/?, h=h/?, l=l/?, c=c/?, v=v*? WHERE sym=? AND tf=? AND t<?", (k, k, k, k, k, sym, tf, t)
            )

    def series(self, sym: str, tf: str) -> Series | None:
        with self.lock:
            r = self.db.execute("SELECT fetched_at FROM series WHERE sym=? AND tf=?", (sym, tf)).fetchone()
            if not r:
                return None
            a, b = self.db.execute("SELECT MIN(t), MAX(t) FROM bars WHERE sym=? AND tf=?", (sym, tf)).fetchone()
        return Series(r[0], a, b)

    def mark_fetched(self, sym: str, tf: str, at: float) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO series VALUES (?,?,?)", (sym, tf, at))

    def intraday_tfs(self, sym: str) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT tf FROM series WHERE sym=? AND tf<>'1d'", (sym,))]

    def symbols_with(self, tf: str) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT sym FROM series WHERE tf=?", (tf,))]

    # ── corporate actions ──
    def splits(self, sym: str) -> list[tuple[int, float, float]]:
        with self.lock:
            return self.db.execute("SELECT t,num,den FROM splits WHERE sym=? ORDER BY t", (sym,)).fetchall()

    def dividends(self, sym: str) -> list[tuple[int, float]]:
        with self.lock:
            return self.db.execute("SELECT t,amount FROM dividends WHERE sym=? ORDER BY t", (sym,)).fetchall()

    def add_events(self, sym: str, splits: list[tuple[int, float, float]], dividends: list[tuple[int, float]]) -> list[tuple[int, float, float]]:
        """Store the events of an answer; returns the splits not seen before."""
        known = {t for t, _, _ in self.splits(sym)}
        new = [s for s in splits if s[0] not in known]
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO splits VALUES (?,?,?,?)", [(sym, *s) for s in splits])
            self.db.executemany("INSERT OR REPLACE INTO dividends VALUES (?,?,?)", [(sym, *d) for d in dividends])
        return new

    # ── meta ──
    def set_meta(self, sym: str, tz: str | None, name: str | None, currency: str | None, exchange: str | None) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?)", (sym, tz, name, currency, exchange))

    def meta(self, sym: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT tz,name,currency,exchange FROM meta WHERE sym=?", (sym,)).fetchone()
        return dict(zip(("tz", "name", "currency", "exchange"), r)) if r else None
