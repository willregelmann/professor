"""A minimal MCP server (stdio, JSON-RPC 2.0) exposing the agent's tools.

Dependency-free on purpose: the protocol surface needed here is four methods.
The store is opened on the first tool call, so a store that can't be opened is
reported through the tool result, where the agent can see it, instead of as a
server that silently failed to start.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from professor import tools
from professor.store import Store

PROTOCOL = "2025-06-18"


# The tools-only host's own two tools. They're offered only when this server is
# started for a lecturer, and they reach the lecture over HTTP on this machine.
LECTURER_TOOLS = [
    {"name": "listen",
     "description": "Give a lecture: wait for the next thing the lecture needs from you (a slide to present, "
                    "a question to answer). Call it to begin, and again every time you have finished "
                    "speaking. It can take a minute to return; if it says there is nothing yet, call it "
                    "again.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "say",
     "description": "Speak one passage to the room, out loud. Two or three sentences at a time. This is the "
                    "only way the room hears you while you are giving a lecture.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
]


def lecture_url() -> str:
    return os.environ.get("PROFESSOR_URL", "http://127.0.0.1:8770").rstrip("/")


def _lecturer_call(name: str, args: dict) -> tuple[str, bool]:
    request = urllib.request.Request(f"{lecture_url()}/host/{name}", method="POST",
                                     data=json.dumps(args).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as reply:
            return json.loads(reply.read())["text"], False
    except urllib.error.HTTPError as e:
        return f"The lecture at {lecture_url()} refused: {e.read().decode(errors='replace')[:300]}", True
    except (OSError, ValueError) as e:
        return (f"No lecture could be reached at {lecture_url()} ({e}). Start one with "
                "`professor serve --deck <number> --host tools`, or set PROFESSOR_URL."), True


class Server:
    def __init__(self, db: Path | str, *, lecturer: bool = False) -> None:
        self.db = db
        self.lecturer = lecturer
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
                                 "inputSchema": t["parameters"]} for t in tools.SCHEMAS]
                      + (LECTURER_TOOLS if self.lecturer else [])}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                if self.lecturer and p.get("name") in ("listen", "say"):
                    text, is_error = _lecturer_call(p["name"], p.get("arguments") or {})
                else:
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


def serve(db: Path | str, stdin=sys.stdin, stdout=sys.stdout, *, lecturer: bool = False) -> None:
    server = Server(db, lecturer=lecturer)
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
