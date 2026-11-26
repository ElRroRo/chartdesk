"""Spreads: a formula over tickers (XLF/SPY, US10Y-US02Y, (AAPL+MSFT)/2, 2*GLD-SLV), evaluated on their bars.

The formula is parsed by a small recursive-descent parser into a tree of numbers, tickers and + - * / (never
eval). Bars: each leg's bars for the same interval, matched by trading date (daily and longer: each leg's date in
its own exchange's zone) or by bar time (intraday); a time where any leg has no bar has no spread bar. The open
and close are the formula on the legs' opens and closes; the high and low are the formula on the legs' highs and
lows, widened to cover the open and close (field by field, as TradingView does: a ratio's true intraday extremes
are not knowable from the legs' bars).

The core spells a share-class marker in lower case ("BAC/PB" -> "BACpB"), which turns "XLF/SPY" into "XLFsPY";
`app_name` undoes it (no ticker chartd serves has a lower-case letter).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)(?![A-Z0-9.!])|([A-Z0-9][A-Z0-9.!]*)|([-+*/()]))")


@dataclass(frozen=True)
class Num:
    v: float


@dataclass(frozen=True)
class Leg:
    ticker: str


@dataclass(frozen=True)
class Op:
    op: str  # "+", "-", "*", "/", "neg"
    a: "Node"
    b: "Node | None" = None


Node = Union[Num, Leg, Op]


class FormulaError(ValueError):
    pass


def app_name(ticker: str) -> str:
    """The core's source spelling back to the app's ("XLFsPY" -> "XLF/SPY")."""
    return "".join(f"/{c.upper()}" if c.islower() else c for c in ticker)


def tokens(text: str) -> list[str]:
    out, i, s = [], 0, text.upper()
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        m = _TOKEN.match(s, i)
        if not m or m.end() == i:
            raise FormulaError(f"cannot read {s[i:]!r}")
        out.append(m.group(1) or m.group(2) or m.group(3))
        i = m.end()
    return out


def parse(text: str) -> Node:
    toks = tokens(text)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def expr() -> Node:
        n = term()
        while peek() in ("+", "-"):
            op = take()
            n = Op(op, n, term())
        return n

    def term() -> Node:
        n = factor()
        while peek() in ("*", "/"):
            op = take()
            n = Op(op, n, factor())
        return n

    def factor() -> Node:
        t = peek()
        if t is None:
            raise FormulaError("formula ends early")
        if t == "-":
            take()
            return Op("neg", factor())
        if t == "(":
            take()
            n = expr()
            if peek() != ")":
                raise FormulaError("unclosed (")
            take()
            return n
        take()
        if re.fullmatch(r"\d+(?:\.\d+)?", t):
            return Num(float(t))
        if t in "+*/)":
            raise FormulaError(f"unexpected {t}")
        return Leg(t)

    n = expr()
    if pos != len(toks):
        raise FormulaError(f"unexpected {toks[pos]}")
    return n


def legs(n: Node) -> list[str]:
    if isinstance(n, Leg):
        return [n.ticker]
    if isinstance(n, Op):
        return legs(n.a) + (legs(n.b) if n.b is not None else [])
    return []


def is_formula(name: str) -> bool:
    """A formula has an operator and parses (a ticker with a dot or a '!' is not one)."""
    if not any(c in name for c in "/*+-()"):
        return False
    try:
        n = parse(name)
    except FormulaError:
        return False
    return isinstance(n, Op) and bool(legs(n))


def evaluate(n: Node, values: dict[str, float]) -> float:
    if isinstance(n, Num):
        return n.v
    if isinstance(n, Leg):
        return values[n.ticker]
    a = evaluate(n.a, values)
    if n.op == "neg":
        return -a
    b = evaluate(n.b, values)  # type: ignore[arg-type]
    if n.op == "+":
        return a + b
    if n.op == "-":
        return a - b
    if n.op == "*":
        return a * b
    if b == 0:
        raise ZeroDivisionError
    return a / b


def combine(n: Node, series: dict[str, dict[int, tuple]], key_out) -> list[tuple]:
    """Legs' bars by key -> spread bars (t, o, h, l, c, 0) for the keys every leg has. `key_out(key)` -> bar time."""
    names = list(series)
    common = set(series[names[0]]) if names else set()
    for k in names[1:]:
        common &= set(series[k])
    out = []
    for key in sorted(common):
        try:
            f = {i: evaluate(n, {name: series[name][key][i] for name in names}) for i in (1, 2, 3, 4)}
        except ZeroDivisionError:
            continue
        o, h, lo, c = f[1], f[2], f[3], f[4]
        out.append((key_out(key), o, max(o, h, lo, c), min(o, h, lo, c), c, 0.0))
    return out
