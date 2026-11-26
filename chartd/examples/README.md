# Python indicators

Each `.py` file here becomes an indicator in Chart Desk (Indicators, Personal, My scripts: "Py: <TITLE>").

```python
TITLE = "Z-score 20"     # shown in the app (default: the file name)
OVERLAY = False          # True: drawn on the price pane
def compute(bars):       # numpy arrays: bars["t"] (Unix ms), bars["o"], ["h"], ["l"], ["c"], ["v"]
    return values        # one value per bar; NaN where there is none
```

The file name is its id (`zscore20.py` -> ZSCORE20). It is computed over the chart's own symbol and interval,
every stored bar from the first, so its warm-up is right. Saving the file is enough: chartd loads it again, and
the app picks up a new file within a minute. numpy is available.
