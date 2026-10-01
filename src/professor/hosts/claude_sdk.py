"""Claude as the lecturer, through the Claude Agent SDK.

The SDK keeps one conversation open for the whole lecture, and with partial
messages turned on its text arrives as it's produced. By default the lecturer is
a bare model: no built-in tools, and nothing from this machine's Claude Code
setup (settings, hooks, memory, other MCP servers) joins the lecture. Given a
directory to work in, it can read the files there to check something live.

The SDK is only imported when a lecture is given this way, so Professor doesn't
need it otherwise: `pip install 'professor[claude]'`.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

READ_ONLY = ["Read", "Glob", "Grep"]


def _client(model: str, effort: str, cwd: Path | None) -> Any:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    except ImportError as exc:
        raise RuntimeError("the Claude Agent SDK isn't installed (pip install 'professor[claude]')") from exc
    tools = READ_ONLY if cwd else []
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        model=model, effort=effort, tools=tools, allowed_tools=tools, setting_sources=[],
        strict_mcp_config=True, include_partial_messages=True, cwd=str(cwd) if cwd else None))


class ClaudeHost:
    def __init__(self, *, model: str = "claude-opus-5-5", effort: str = "medium", cwd: Path | str | None = None,
                 client: Callable[[], Any] | None = None, drain_timeout_s: float = 20):
        self._make = client or (lambda: _client(model, effort, Path(cwd).resolve() if cwd else None))
        self._drain_timeout_s = drain_timeout_s
        self._client: Any = None
        self._mid_turn = False  # a turn was started and its end hasn't been read yet
        self.session = ""

    async def turn(self, prompt: str) -> AsyncIterator[str]:
        if self._client is None:
            client = self._make()
            await client.connect()
            self._client = client
        if self._mid_turn:
            await self._drain()
        await self._client.query(prompt)
        self._mid_turn = True
        async for msg in self._client.receive_response():
            event = getattr(msg, "event", None)
            if isinstance(event, dict):  # a piece of the reply, as it's produced
                delta = event.get("delta") or {}
                if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    yield delta["text"]
            elif hasattr(msg, "session_id") and hasattr(msg, "is_error"):  # the end of the turn
                self._mid_turn = False
                self.session = msg.session_id or self.session
                if msg.is_error:
                    raise RuntimeError(f"Claude ended the turn with an error ({getattr(msg, 'subtype', 'unknown')})")

    async def _drain(self) -> None:
        """The last turn was cut short. Read past what's left of it, so its tail
        isn't mistaken for the start of the next reply."""
        async def read() -> None:
            async for _ in self._client.receive_response():
                pass
        try:
            await asyncio.wait_for(read(), self._drain_timeout_s)
        finally:
            self._mid_turn = False

    async def cancel(self) -> None:
        """Interrupt the turn in progress."""
        if self._client is not None and self._mid_turn:
            await self._client.interrupt()

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.disconnect()
