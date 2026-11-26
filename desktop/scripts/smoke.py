"""Start the staged chartd and chart server the way the desktop app does, and check they answer with real data.

Usage (from the repo root, after stage.py): python desktop/scripts/smoke.py
Exits non-zero when a check fails. Uses ports 8798/8799 and a throwaway data folder.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "desktop" / "stage"
WIN = sys.platform == "win32"


def get(url: str, timeout: float = 30) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError:
        return 0, b""


def wait(url: str, secs: int = 60) -> bool:
    for _ in range(secs * 2):
        if get(url, 2)[0] == 200:
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="chartdesk-smoke-"))
    py = STAGE / "python" / ("python.exe" if WIN else "bin/python3")
    server = STAGE / "bin" / ("opentrader-server.exe" if WIN else "opentrader-server")
    env = {**os.environ, "CHARTD_DATA": str(tmp / "chartd"), "CHARTD_NOTIFY": "off", "PYTHONNOUSERSITE": "1"}
    procs = [subprocess.Popen([str(py), "-m", "chartd.server", "--port", "8798"], env=env,
                              stdout=open(tmp / "chartd.log", "w"), stderr=subprocess.STDOUT)]
    senv = {**os.environ, "OPENTRADER_BIND": "127.0.0.1:8799", "OPENTRADER_STATIC_DIR": str(STAGE / "web"),
            "OPENTRADER_CACHE_DIR": str(tmp / "cache"), "OPENTRADER_GATEWAY_TOKEN": "local",
            "OPENTRADER_SCREENER_FEED": "off", "OPENTRADER_RATE_PER_MIN": "0"}
    procs.append(subprocess.Popen([str(server)], env=senv, stdout=open(tmp / "server.log", "w"), stderr=subprocess.STDOUT))
    fails = []
    try:
        if not wait("http://127.0.0.1:8798/health"):
            fails.append("chartd did not answer /health")
        if not wait("http://127.0.0.1:8799/"):
            fails.append("the chart server did not serve the app")
        for t in ("AAPL", "BTCUSDT"):
            s, body = get(f"http://127.0.0.1:8798/market/v1/bars/{t}?tf=1d&from=2026-01-01&to=2030-01-01&order=desc&limit=5", 60)
            n = len(json.loads(body).get("t", [])) if s == 200 else 0
            print(f"{t}: HTTP {s}, {n} bars", flush=True)
            if n == 0:
                fails.append(f"no {t} bars")
        s, body = get("http://127.0.0.1:8798/market/v1/snapshot?tickers=AAPL", 60)
        print(f"snapshot: HTTP {s}", flush=True)
        if s != 200:
            fails.append("no snapshot")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
    if fails:
        for name in ("chartd.log", "server.log"):
            print(f"--- {name}\n" + (tmp / name).read_text(errors="replace")[-3000:])
        print("FAILED: " + "; ".join(fails))
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
