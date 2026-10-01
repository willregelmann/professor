"""What every host adapter does (invariants/interfaces/HOST.md).

An adapter starts a turn, hands back the agent's text as it's produced, can cancel
the turn, and holds whatever handle the host uses to keep its conversation going.
It contains no lecture logic.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class Host(Protocol):
    session: str  # the host's own handle; "" until it has given one

    def turn(self, prompt: str) -> AsyncIterator[str]:
        """Start a turn and yield the agent's text as it's produced."""
        ...

    async def cancel(self) -> None:
        """Stop the turn in progress. Text already yielded stands."""
        ...
