"""The app's MCP endpoint (coding agents: Claude Code, AGY) on chartd, relayed to the open Chart Desk window.

Upstream's desktop app answers MCP itself (src-tauri agent.rs): Rust carries each JSON-RPC message to the window,
which runs the tools with the app's own stores (src/agent/). Without that shell, chartd carries them: the window
keeps a WebSocket to /chartdesk/v1/agent; a POST to /mcp (streamable HTTP, JSON answers) is handed to it and its
answer returned. Every request needs the token in ~/.config/chartdesk/mcp-token (made on first start, mode 600) as
"Authorization: Bearer <token>", and a request from a web page (an Origin that is not this machine) is refused.
With no window open, the window's last answers to the describing methods (initialize, tools/list, resources/list,
prompts/list; kept in <data>/agent_cache.json) are given, so the client stays connected, and a tool call is told
to start Chart Desk.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import secrets
from pathlib import Path

from aiohttp import WSMsgType, web

REPLY_TIMEOUT = 90.0
DESCRIBING = ("initialize", "tools/list", "resources/list", "prompts/list")
LOCAL_ORIGINS = ("http://127.0.0.1", "http://localhost")


def token_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "chartdesk" / "mcp-token"


def load_token() -> str:
    p = token_path()
    if p.exists():
        t = p.read_text().strip()
        if t:
            return t
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    p.write_text(t + "\n")
    p.chmod(0o600)
    return t


class AgentRelay:
    def __init__(self, dir: Path | None = None) -> None:
        self.cache_path = (dir / "agent_cache.json") if dir else None
        self.cache: dict[str, dict] = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text())
            except ValueError:
                self.cache = {}
        self.token = load_token()
        self.ws: web.WebSocketResponse | None = None
        self.waiting: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)

    async def h_window(self, req: web.Request) -> web.StreamResponse:
        """The window's side: one WebSocket; the latest window to connect answers."""
        origin = req.headers.get("Origin", "")
        if origin and not origin.startswith(LOCAL_ORIGINS):
            return web.Response(status=403)
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(req)
        self.ws = ws
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                fut = self.waiting.pop(int(m.get("id", -1)), None)
                if fut and not fut.done():
                    fut.set_result(str(m.get("reply") or ""))
        finally:
            if self.ws is ws:
                self.ws = None
        return ws

    async def h_mcp(self, req: web.Request) -> web.Response:
        origin = req.headers.get("Origin", "")
        if origin and not origin.startswith(LOCAL_ORIGINS):
            return web.Response(status=403, text="refused: a web page may not call the agent endpoint")
        if req.headers.get("Authorization", "") != f"Bearer {self.token}":
            return web.Response(status=401, text="missing or wrong token (~/.config/chartdesk/mcp-token)")
        if req.method != "POST":
            return web.Response(status=405, headers={"Allow": "POST"})
        body = await req.text()
        try:
            msg = json.loads(body) if body.strip().startswith("{") else {}
        except ValueError:
            msg = {}
        msg_id, method = msg.get("id"), msg.get("method")
        if self.ws is None or self.ws.closed:
            if msg_id is None:
                return web.Response(status=202)
            if method in self.cache:
                return web.json_response({**self.cache[method], "id": msg_id})
            return web.json_response({"jsonrpc": "2.0", "id": msg_id,
                                      "error": {"code": -32000, "message": "Chart Desk is not open: start it, then try again"}})
        rid = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.waiting[rid] = fut
        await self.ws.send_str(json.dumps({"id": rid, "body": body}))
        try:
            reply = await asyncio.wait_for(fut, REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            self.waiting.pop(rid, None)
            return web.json_response({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32001, "message": "the app took too long to answer"}})
        if not reply:
            return web.Response(status=202)  # a notification
        if method in DESCRIBING:
            try:
                ans = json.loads(reply)
                if "result" in ans:
                    self.cache[method] = {k: v for k, v in ans.items() if k != "id"}
                    if self.cache_path:
                        self.cache_path.write_text(json.dumps(self.cache))
            except ValueError:
                pass
        return web.Response(text=reply, content_type="application/json")
