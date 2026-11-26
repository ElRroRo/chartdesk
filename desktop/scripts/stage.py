"""Gather what the desktop app ships into desktop/stage/, on the platform it is built for:

  stage/web      the app's web build (vite --mode web)
  stage/bin      the chart server (cargo build --release -p opentrader-server)
  stage/python   a standalone Python (uv's python-build-standalone copy) with chartd and its packages installed

Usage (from the repo root): python desktop/scripts/stage.py [--skip-web] [--skip-server]
Needs uv, node/npm (the web build) and cargo (the server). The web build needs ../lightweight-charts-drawing.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "desktop" / "stage"
WIN = sys.platform == "win32"
PY = "3.13"
#: Parts of the standard library chartd never loads (tests, the Tk GUI, IDLE): about 40 MB less.
DROP = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data")


def run(*cmd, cwd=ROOT, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, shell=WIN and str(cmd[0]) in ("npx", "npm"), **kw)


def web():
    out = STAGE / "web"
    shutil.rmtree(out, ignore_errors=True)
    run("npx", "vite", "build", "--mode", "web", "--outDir", out, "--emptyOutDir")
    # A personal TradingView setup never ships.
    shutil.rmtree(out / "chartdesk", ignore_errors=True)


def server():
    run("cargo", "build", "--release", "-p", "opentrader-server", cwd=ROOT / "src-tauri")
    name = "opentrader-server.exe" if WIN else "opentrader-server"
    (STAGE / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "src-tauri" / "target" / "release" / name, STAGE / "bin" / name)


def python():
    out = STAGE / "python"
    shutil.rmtree(out, ignore_errors=True)
    run("uv", "python", "install", PY)
    exe = Path(subprocess.run(["uv", "python", "find", PY], check=True, capture_output=True, text=True).stdout.strip()).resolve()
    base = exe.parent if WIN else exe.parent.parent
    print(f"  python from {base}", flush=True)
    shutil.copytree(base, out, symlinks=True)
    lib = out / "Lib" if WIN else out / "lib" / f"python{PY}"
    (lib / "EXTERNALLY-MANAGED").unlink(missing_ok=True)
    for d in DROP:
        shutil.rmtree(lib / d, ignore_errors=True)
    for p in out.glob("lib/libtcl*"):
        p.unlink()
    for p in list(out.glob("lib/tcl*")) + list(out.glob("lib/tk*")) + list(out.glob("tcl")):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
    target = out / "python.exe" if WIN else out / "bin" / "python3"
    run("uv", "pip", "install", "--python", target, "--break-system-packages", "--no-cache", "--compile-bytecode", ROOT / "chartd")
    # What only building or installing packages needs (pip, C headers, man pages) and numpy's own tests.
    site = lib / "site-packages"
    for d in [site / "pip", out / "include", out / "share", *site.glob("pip-*.dist-info"), *site.glob("numpy/**/tests")]:
        shutil.rmtree(d, ignore_errors=True)
    run(target, "-c", "import chartd.server, numpy, curl_cffi, aiohttp, zoneinfo; zoneinfo.ZoneInfo('America/New_York'); print('chartd imports OK')")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--skip-web", action="store_true")
    a.add_argument("--skip-server", action="store_true")
    a.add_argument("--skip-python", action="store_true")
    args = a.parse_args()
    STAGE.mkdir(parents=True, exist_ok=True)
    if not args.skip_web:
        web()
    if not args.skip_server:
        server()
    if not args.skip_python:
        python()
    print("staged:", ", ".join(sorted(p.name for p in STAGE.iterdir())))


if __name__ == "__main__":
    os.environ.setdefault("UV_PYTHON_PREFERENCE", "only-managed")
    main()
