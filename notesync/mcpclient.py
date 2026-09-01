"""Minimal MCP client over stdio, built for `mcp-remote` (DESIGN.md #9).

Spawns the same command line the agent clients use --
`npx -y mcp-remote https://mcp.evernote.com/mcp` -- so it shares the OAuth
credentials in ~/.mcp-auth. One authorization serves every client; this
module contains zero OAuth code by design.
"""
import json
import select
import subprocess
import time


class McpError(Exception):
    pass


class McpStdioClient:
    def __init__(self, cmd, stderr_path=None, timeout=120):
        self.timeout = timeout
        self._id = 0
        stderr = open(stderr_path, "ab") if stderr_path else subprocess.DEVNULL
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=stderr)
        self._initialize()

    # -- wire ------------------------------------------------------------
    def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _readline(self, timeout):
        r, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not r:
            raise McpError(f"mcp-remote: no response within {timeout}s")
        line = self.proc.stdout.readline()
        if not line:
            raise McpError("mcp-remote: connection closed")
        return line

    def _rpc(self, method, params, timeout=None):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params})
        # absolute deadline: unrelated frames must not keep resetting the
        # clock, or a wedged upstream session blocks a cycle forever
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise McpError(f"{method}: no response within deadline")
            line = self._readline(remain)
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # stray non-JSON output
            if msg.get("method") and msg.get("id") is not None:
                # server->client request (e.g. ping): answer politely
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                continue
            if msg.get("method"):
                continue  # notification
            if msg.get("id") != rid:
                continue  # stale response
            if "error" in msg:
                raise McpError(str(msg["error"]))
            return msg.get("result", {})

    # -- protocol --------------------------------------------------------
    def _initialize(self):
        self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "notesync", "version": "0.1.0"},
        }, timeout=180)  # first run may download npm packages / refresh tokens
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool, arguments):
        """tools/call -> structuredContent when present (the machine payload;
        the text content is a human-readable rendering), else parsed text."""
        res = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        texts = [c.get("text", "") for c in res.get("content", [])
                 if c.get("type") == "text"]
        body = "\n".join(texts)
        if res.get("isError"):
            raise McpError(f"{tool}: {body[:500]}")
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        try:
            return json.loads(body)
        except ValueError:
            return {"text": body}

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
