"""A host with tools and nothing else: an agent that can call tools but whose
words can't be read as it writes them, such as an ordinary Claude Code session.

The agent speaks by calling `say` with a passage, and waits for the next moment
by calling `listen`, which returns when the lecture has something for it to do.
A passage is heard only once its whole tool call has arrived, so this is slower
to the first word than a host that streams, and the page shows the lecturer
composing in the meantime.

`say` and `listen` belong to this adapter. They aren't part of the agent's tool
set, and no other host exposes them.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

HOW_TO_BE_HEARD = (
    "HOW YOU ARE HEARD. The room hears you ONLY through the say tool. Anything you write outside it is "
    "lost. Call say with one passage at a time, two or three sentences, and call it the moment each "
    "passage is ready rather than composing everything first. A block (a diagram, a mark) goes inside "
    "the passage it belongs to. When you have finished what you were asked to do, call listen and wait: "
    "it returns the next thing to do. Never end your turn; always finish by calling listen."
)
REMINDER = "Speak through say, a passage at a time, then call listen."
NOTHING_YET = ("Nothing yet. The lecture hasn't started, or the room is still hearing what you said. "
               "Call listen again and keep waiting.")
STOPPED = ("Not heard. A listener stopped you, or the lecture moved on, so the room did not hear that "
           "passage. Say nothing more now. Call listen to find out what to do next.")
NOT_YOUR_TURN = "Not heard: it isn't your turn to speak. Call listen and wait for the next thing to do."


class ToolsOnlyHost:
    def __init__(self, *, wait_for_agent_s: float = 20, listen_s: float = 50):
        self.wait_for_agent_s = wait_for_agent_s
        self.listen_s = listen_s
        self.session = ""
        self._prompts: asyncio.Queue[str] = asyncio.Queue()
        self._passages: asyncio.Queue[str | None] = asyncio.Queue()
        self._listening = asyncio.Event()  # the agent is waiting in listen right now
        self._speaking = False             # the agent has been handed a turn and hasn't finished it
        self._cancelled = False
        self._told = False                 # the agent has been told how it's heard

    # The agent's side: what its two tools do.

    async def listen(self) -> str:
        """The agent is ready for the next thing to do. Returns the prompt, or
        NOTHING_YET if there isn't one before the wait runs out."""
        if self._speaking:  # calling listen is how the agent ends its turn
            self._speaking = False
            self._passages.put_nowait(None)
        self.session = self.session or "tools-only"
        self._listening.set()
        try:
            prompt = await asyncio.wait_for(self._prompts.get(), self.listen_s)
        except TimeoutError:
            return NOTHING_YET
        finally:
            self._listening.clear()
        self._speaking = True
        told, self._told = self._told, True
        return prompt + "\n\n" + (REMINDER if told else HOW_TO_BE_HEARD)

    async def say(self, text: str) -> str:
        """The agent speaks a passage. Returns what became of it."""
        if self._cancelled:
            return STOPPED
        if not self._speaking:
            return NOT_YOUR_TURN
        self._passages.put_nowait(text)
        return "Heard."

    # The lecture's side.

    async def turn(self, prompt: str) -> AsyncIterator[str]:
        self._cancelled = False
        for queue in (self._prompts, self._passages):  # nothing is left over from a turn that was cut short
            while not queue.empty():
                queue.get_nowait()
        if not self._listening.is_set():
            try:
                await asyncio.wait_for(self._listening.wait(), self.wait_for_agent_s)
            except TimeoutError:
                raise RuntimeError("no agent is listening for this lecture; start one and have it call "
                                   "the listen tool") from None
        self._prompts.put_nowait(prompt)
        while (text := await self._passages.get()) is not None:
            yield text + "\n"

    async def cancel(self) -> None:
        """Stop hearing the turn in progress. The agent is told when it next speaks."""
        self._cancelled = True
        self._speaking = False
        while not self._prompts.empty():
            self._prompts.get_nowait()
