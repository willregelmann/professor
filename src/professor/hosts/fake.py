"""A host with no model behind it: replays scripted replies at a set pace.

It lets the core, the page and the harness be tested without an agent, and gives
the latency bench a floor to compare real hosts against.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class FakeHost:
    def __init__(self, replies: list[str], *, chars_per_s: float = 400.0, chunk: int = 12,
                 first_delay_s: float = 0.0, failures: dict[int, int] | None = None):
        """`failures` makes chosen calls fail like a host that can't be reached:
        call number (from 0) -> how many characters it gets out first."""
        self.replies = list(replies)
        self.failures = failures or {}
        self._given = 0  # replies given in full
        self.chars_per_s = chars_per_s
        self.chunk = chunk
        self.first_delay_s = first_delay_s
        self.session = ""
        self.prompts: list[str] = []  # what each turn was asked, for tests to read
        self._cancelled = False

    async def turn(self, prompt: str) -> AsyncIterator[str]:
        call = len(self.prompts)
        self.prompts.append(prompt)
        if self._given >= len(self.replies):
            raise RuntimeError(f"fake host has no reply scripted for turn {self._given + 1}")
        reply = self.replies[self._given]
        fail_after = self.failures.get(call)
        self._cancelled = False
        await asyncio.sleep(self.first_delay_s)
        if fail_after == 0:
            raise ConnectionError("connection refused")
        self.session = self.session or "fake-session"
        for i in range(0, len(reply), self.chunk):
            if self._cancelled:
                return
            if fail_after is not None and i >= fail_after:
                raise ConnectionError("connection lost")
            piece = reply[i:i + self.chunk]
            yield piece
            await asyncio.sleep(len(piece) / self.chars_per_s)
        self._given += 1

    async def cancel(self) -> None:
        self._cancelled = True


class NotesReader(FakeHost):
    """A lecturer with no model behind it, for trying the page out: it reads each
    slide's notes out as written, and says so when it's asked something."""

    def __init__(self, *, chars_per_s: float = 250.0):
        super().__init__([], chars_per_s=chars_per_s)

    async def turn(self, prompt: str) -> AsyncIterator[str]:
        _, found, rest = prompt.partition("then stop):\n")
        if found:
            reply = rest.split("\n\n")[0]
        elif "could not be shown" in prompt:
            reply = "I can only read my notes out, so I will carry on without that picture."
        else:
            reply = ("I am a script with no model behind me, reading the notes out, so I can't answer that. "
                     "Give this deck through a real host to have questions answered.")
        self.replies, self._given = [reply], 0
        async for piece in super().turn(prompt):
            yield piece
