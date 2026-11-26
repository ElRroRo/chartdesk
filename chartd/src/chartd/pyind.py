"""Python indicators: a file in ~/chartdesk/indicators (CHARTD_INDICATORS) becomes a series every chart can use.

A file declares, at its top level:

    TITLE = "RSI 2"          # shown in the app (default: the file name)
    OVERLAY = False          # on the price pane, or a pane of its own
    def compute(bars):       # bars: numpy arrays bars["t"] (Unix ms), ["o"], ["h"], ["l"], ["c"], ["v"]
        return values        # one value per bar (NaN: none)

Its id is the file name in capitals (rsi2.py -> RSI2). chartd serves it as the symbol PY:PY.<ID>.<TICKER>: the
indicator computed over <TICKER>'s bars of the asked interval (all stored bars up to the end of the range, so its
warm-up is right), one bar per value (open = high = low = close). The app keeps a generated OakScript indicator for
each file ("Py: <TITLE>", My scripts) that plots request.security("PY:PY.<ID>." + syminfo.ticker, ...): so a Python
indicator plots on any chart, takes alerts and runs in the script screener. A file is loaded again when it changes.
The files are the owner's own code and run inside chartd (numpy is available).
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import numpy as np

PREFIX = "PY."
_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")


def folder() -> Path:
    return Path(os.environ.get("CHARTD_INDICATORS") or Path.home() / "chartdesk" / "indicators")


class PyIndicators:
    def __init__(self) -> None:
        self._mods: dict[str, tuple[float, object]] = {}

    def _load(self, path: Path):
        ind = path.stem.upper()
        mtime = path.stat().st_mtime
        hit = self._mods.get(ind)
        if hit and hit[0] == mtime:
            return hit[1]
        spec = importlib.util.spec_from_file_location(f"chartd_py_{ind.lower()}", path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if not callable(getattr(mod, "compute", None)):
            raise ValueError(f"{path.name}: no compute(bars)")
        self._mods[ind] = (mtime, mod)
        return mod

    def listing(self) -> list[dict]:
        out = []
        d = folder()
        if not d.is_dir():
            return out
        for p in sorted(d.glob("*.py")):
            ind = p.stem.upper()
            if p.stem.startswith("_") or not _ID.match(ind):
                continue
            try:
                m = self._load(p)
                out.append({"id": ind, "title": str(getattr(m, "TITLE", p.stem)), "overlay": bool(getattr(m, "OVERLAY", False)),
                            "mtime": p.stat().st_mtime, "error": None})
            except Exception as e:  # shown in the app as a script that fails, with this reason
                out.append({"id": ind, "title": p.stem, "overlay": False, "mtime": p.stat().st_mtime, "error": f"{type(e).__name__}: {e}"})
        return out

    @staticmethod
    def split(ticker: str) -> tuple[str, str] | None:
        """"PY.RSI2.AAPL" -> ("RSI2", "AAPL"); None when not a Python indicator symbol."""
        if not ticker.upper().startswith(PREFIX):
            return None
        rest = ticker[len(PREFIX):]
        ind, _, under = rest.partition(".")
        return (ind.upper(), under) if ind and under else None

    def compute(self, ind: str, rows: list[tuple]) -> list[tuple]:
        path = folder() / f"{ind.lower()}.py"
        if not path.exists():
            raise FileNotFoundError(f"no indicator {ind} in {folder()}")
        m = self._load(path)
        a = np.array(rows, dtype=float) if rows else np.zeros((0, 6))
        bars = {k: a[:, i] for i, k in enumerate("tohlcv")}
        vals = np.asarray(m.compute(bars), dtype=float)
        if vals.shape != (len(rows),):
            raise ValueError(f"{ind}: compute returned {vals.shape}, wanted {len(rows)} values")
        return [(int(r[0]), v, v, v, v, 0.0) for r, v in zip(rows, vals) if np.isfinite(v)]
