# Chart Desk

A free charting app for your desktop: candles, 80+ drawing tools, about 1,800 indicators, compare and spreads, a stock screener, alerts, bar replay and saved layouts. No account, no ads, no subscription. Everything runs on your own computer.

It covers US stocks, ETFs and indices, futures, forex, crypto, US Treasury yields, economic series, and Borsa Istanbul.

## Download

Get the file for your computer from the [latest release](https://github.com/ElRroRo/chartdesk/releases/latest):

| Computer | File |
|---|---|
| Windows 10 or 11 | `ChartDesk-<version>-Windows-x64-Setup.exe` |
| Mac with Apple Silicon (M1 and later) | `ChartDesk-<version>-macOS-arm64.dmg` |
| Mac with an Intel chip | `ChartDesk-<version>-macOS-x64.dmg` |
| Linux | `ChartDesk-<version>-Linux-x86_64.AppImage` |

On a Mac, the Apple menu > About This Mac shows which chip you have.

### Opening it the first time

Chart Desk is not signed with a paid Apple or Microsoft certificate, so both systems warn you once.

- **Windows:** run the installer. If "Windows protected your PC" appears, click **More info**, then **Run anyway**.
- **Mac:** open the `.dmg` and drag Chart Desk into Applications. Open it once: macOS says it cannot check it. Then go to System Settings > Privacy & Security, scroll down, click **Open Anyway** next to Chart Desk, and confirm. From then on it opens normally.
  If macOS instead says the app "is damaged", run this once in Terminal: `xattr -dr com.apple.quarantine "/Applications/Chart Desk.app"`
- **Linux:** make the AppImage executable (`chmod +x ChartDesk-*.AppImage`) and run it.

When a new version is out, Chart Desk shows a notification with a link to it.

## What to expect

- **Prices** come from public sources: Yahoo Finance (stocks, ETFs, indices, futures, forex), Binance (crypto, live), FRED (Treasury yields and economic series), and Is Yatirim and KAP (Borsa Istanbul). Each chart shows how many minutes its prices are delayed. Crypto, forex and US stocks are live (US stocks on Nasdaq's real-time prices); futures are about 10 minutes behind; Borsa Istanbul is 15 minutes behind; yields and economic series are daily.
- **Intervals:** 1 minute up to monthly. Yahoo keeps 1-minute bars for about a week and hourly bars for two years. Daily history goes back decades.
- **Symbols:** search by ticker or name. TradingView-style names work too, such as `US100`, `US500`, `GOLD`, `EURUSD` or `US10Y`; crypto pairs are Binance's, such as `BTCUSDT`. Type a formula such as `XLF/SPY` or `US10Y-US02Y` to chart a spread.
- **The stock screener** needs the daily history of the whole US market. The first time you open it, it offers to download it: about 11,700 symbols, 2 GB on disk, roughly 45 minutes in the background. After that it updates itself each evening.
- **Alerts:** closing the window keeps Chart Desk in the tray (the menu bar on a Mac), so alerts keep firing. Quit it from there.
- **Your data stays on your computer.** The window itself cannot reach the internet. Only the bundled data server fetches prices, from the sources above. No account, no usage statistics.

Not included: real-time exchange feeds, tick or second charts, order-flow footprints, company financials, and trading through a broker.

## Python indicators

Put a Python file in the `chartdesk/indicators` folder in your home folder (tray menu > Python indicators folder):

```python
TITLE = "Z-score 20"
OVERLAY = False

def compute(bars):          # numpy arrays: bars["t"], ["o"], ["h"], ["l"], ["c"], ["v"]
    c = bars["c"]
    ...
    return values           # one value per bar
```

Within a minute it appears under Indicators > My scripts as "Py: Z-score 20" and works on any chart, in alerts and in the script screener. numpy is available.

## For coding agents (MCP)

Chart Desk serves the Model Context Protocol at `http://127.0.0.1:8798/mcp` while it runs: an agent such as Claude Code can open symbols, add indicators, draw, set alerts, run screens and take screenshots of the chart. Every request needs the token in `~/.config/chartdesk/mcp-token` as `Authorization: Bearer <token>`. For clients that start a program instead, `chartd/tools/mcp_stdio.py` bridges standard input and output to it.

## Build it yourself

You need Node 22, Rust, and [uv](https://docs.astral.sh/uv/), with [lightweight-charts-drawing](https://github.com/deepentropy/lightweight-charts-drawing) checked out next to this folder.

```sh
npm ci
python desktop/scripts/stage.py          # web build, chart server, Python with chartd
cd desktop && npm ci && npx electron-builder --publish never
```

The installers land in `desktop/release/`. `.github/workflows/desktop.yml` builds all four on GitHub for each `v*` tag.

How it fits together: an Electron window shows the app's web build, served by the chart server (`src-tauri/crates/server`, Rust, port 8799). The chart server reads all market data from `chartd` (`chartd/`, Python, port 8798), which fetches from the sources above and keeps a local SQLite store.

## Credits and licence

Chart Desk is a fork of [OpenTrader](https://github.com/deepentropy/opentrader) by deepentropy, under the MIT licence (see `LICENSE`). Charts are drawn with [TradingView Lightweight Charts™](https://github.com/tradingview/lightweight-charts), Copyright © 2025 TradingView, Inc., [tradingview.com](https://www.tradingview.com/), under the Apache License 2.0. Drawing tools come from [lightweight-charts-drawing](https://github.com/deepentropy/lightweight-charts-drawing) (MIT). See `NOTICE`.

Chart Desk is not affiliated with, endorsed by or connected to TradingView, Inc., or any data provider. Data is fetched by each user's own computer for personal use and comes with no guarantee of accuracy. Nothing here is investment advice.
