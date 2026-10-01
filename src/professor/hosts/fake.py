"""A host with no model behind it: replays scripted replies at a set pace.

It lets the core, the page and the harness be tested without an agent, and gives
the latency bench a floor to compare real hosts against.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class FakeHost:
    def __init__(self, replies: list[str], *, chars_per_s: float = 400.0, chunk: int = 12,
                 first_delay_s: float = 0.0):
        self.replies = list(replies)
        self.chars_per_s = chars_per_s
        self.chunk = chunk
        self.first_delay_s = first_delay_s
        self.session = ""
        self.prompts: list[str] = []  # what each turn was asked, for tests to read
        self._cancelled = False

    async def turn(self, prompt: str) -> AsyncIterator[str]:
        if len(self.prompts) >= len(self.replies):
            raise RuntimeError(f"fake host has no reply scripted for turn {len(self.prompts) + 1}")
        reply = self.replies[len(self.prompts)]
        self.prompts.append(prompt)
        self.session = self.session or "fake-session"
        self._cancelled = False
        await asyncio.sleep(self.first_delay_s)
        for i in range(0, len(reply), self.chunk):
            if self._cancelled:
                return
            piece = reply[i:i + self.chunk]
            yield piece
            await asyncio.sleep(len(piece) / self.chars_per_s)

    async def cancel(self) -> None:
        self._cancelled = True
