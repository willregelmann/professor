"""A minimal MCP server (stdio, JSON-RPC 2.0) exposing the agent's tools.

Dependency-free on purpose: the protocol surface needed here is four methods.
The store is opened on the first tool call, so a store that can't be opened is
reported through the tool result, where the agent can see it, instead of as a
server that silently failed to start.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from professor import tools
from professor.store import Store

PROTOCOL = "2025-06-18"


class Server:
    def __init__(self, db: Path | str) -> None:
        self.db = db
        self._tools: tools.Tools | None = None

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notifications need no reply
            return None
        if method == "initialize":
            result: Any = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "professor", "version": "0.0.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": t["name"], "description": t["description"],
                                 "inputSchema": t["parameters"]} for t in tools.SCHEMAS]}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                if self._tools is None:
                    self._tools = tools.Tools(Store(self.db))
                text, is_error = self._tools.call(p.get("name", ""), p.get("arguments") or {})
            except Exception as e:
                text = json.dumps({"error": f"professor isn't available ({type(e).__name__}: {e}); "
                                            "nothing was read or saved"})
                is_error = True
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        else:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(db: Path | str, stdin=sys.stdin, stdout=sys.stdout) -> None:
    server = Server(db)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = server.handle(json.loads(line))
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
