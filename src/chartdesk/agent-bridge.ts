/*
 * Chart Desk: the window side of the MCP endpoint chartd serves (chartd/agent.py). The window keeps a WebSocket to
 * chartd and answers each JSON-RPC message with the app's own agent tools (src/agent/, the same server the desktop
 * shell's endpoint uses); it reconnects every few seconds when chartd restarts.
 */
import { handleMessage, type AgentServerDef } from "../agent/mcp";

const URL = "ws://127.0.0.1:8798/chartdesk/v1/agent";

/** Agent tools that need what this build does not serve (the community server, filings, fundamentals). */
const UNSERVED = /^(list_community_posts|read_community_post|open_community_post|get_fundamentals|get_revenue_breakdown|list_filings|read_filing)$/;

export function installChartdAgentBridge(full: AgentServerDef): () => void {
  const server: AgentServerDef = { ...full, tools: full.tools.filter((t) => !UNSERVED.test(t.name)) };
  let ws: WebSocket | null = null;
  let stopped = false;
  let timer: number | undefined;
  const open = () => {
    if (stopped) return;
    ws = new WebSocket(URL);
    ws.onmessage = async (e) => {
      let id: unknown = null;
      let reply: string;
      try {
        const m = JSON.parse(String(e.data)) as { id: number; body: string };
        id = m.id;
        reply = await handleMessage(server, m.body);
      } catch (err) {
        reply = JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32603, message: String(err) } });
      }
      if (id != null) ws?.send(JSON.stringify({ id, reply }));
    };
    ws.onclose = () => {
      if (!stopped) timer = window.setTimeout(open, 3000);
    };
    ws.onerror = () => ws?.close();
  };
  open();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
    ws?.close();
  };
}
