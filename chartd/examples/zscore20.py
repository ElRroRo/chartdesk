"""How far today's close is from its 20-day mean, in standard deviations (an example Python indicator)."""
import numpy as np

TITLE = "Z-score 20"
OVERLAY = False


def compute(bars):
    c = bars["c"]
    out = np.full(len(c), np.nan)
    for i in range(19, len(c)):
        w = c[i - 19 : i + 1]
        sd = w.std()
        out[i] = (c[i] - w.mean()) / sd if sd else np.nan
    return out
