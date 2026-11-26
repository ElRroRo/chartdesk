"""Chart Desk's MCP endpoint over stdio, for clients that start a process (Claude Code, AGY).

Each JSON-RPC line read from stdin is POSTed to chartd's /mcp with the token from ~/.config/chartdesk/mcp-token; the
answer is written as one line to stdout (a notification has none). chartd relays it to the open Chart Desk window.
Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("CHARTDESK_MCP_URL", "http://127.0.0.1:8798/mcp")
TOKEN_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "chartdesk" / "mcp-token"


def answer(line: str) -> str | None:
    try:
        msg_id = json.loads(line).get("id")
    except ValueError:
        msg_id = None
    try:
        token = TOKEN_FILE.read_text().strip()
        req = urllib.request.Request(URL, data=line.encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode()
            return body if r.status != 202 and body.strip() else None
    except (OSError, urllib.error.URLError) as e:
        if msg_id is None:
            return None
        why = "chartd is not running (systemctl --user start chartd)" if isinstance(e, urllib.error.URLError) else str(e)
        return json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": why}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        out = answer(line)
        if out:
            sys.stdout.write(out.replace("\n", " ") + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
