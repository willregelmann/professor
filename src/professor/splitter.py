"""Separates what's said from what's drawn, in order, as the agent's text arrives.

The agent writes plain spoken prose, with anything to be drawn inside a fenced
block. `feed` takes text as it's produced and returns whatever is now complete:
sentences to speak and blocks to draw, in the order they were written.

Carried over from the prototype (prototype/server.py), with its lessons:
- a sentence under MIN_SENTENCE characters is merged with the next, because a
  short opener ("Hello.") once held a whole reply back;
- text that might be the start of a fence is held until it's known to be speech
  or a block, so a partial fence is never spoken;
- an unterminated block is still drawn, never spoken.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FENCE_OPEN = "```mermaid"
FENCE = "```"
MIN_SENTENCE = 20
SENTENCE_END = re.compile(r"[.!?](?=\s)")


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Draw:
    code: str


def _speakable(text: str) -> str:
    """Never read markup aloud: a stray backtick is noise to a listener."""
    return text.replace("`", "").strip()


def _held_back(text: str) -> int:
    """How many trailing characters could still turn out to be the start of a fence."""
    for n in range(min(len(text), len(FENCE_OPEN) - 1), 0, -1):
        if FENCE_OPEN.startswith(text[-n:]):
            return n
    return 0


class Splitter:
    def __init__(self) -> None:
        self._buf = ""
        self._in_block = False

    def _cut(self, text: str, out: list[Say | Draw], *, flush: bool) -> str:
        start = 0
        for m in SENTENCE_END.finditer(text):
            if len(text[start:m.end()].strip()) >= MIN_SENTENCE:
                out.append(Say(_speakable(text[start:m.end()])))
                start = m.end()
        rest = text[start:]
        if flush and _speakable(rest):
            out.append(Say(_speakable(rest)))
            rest = ""
        return "" if flush else rest

    def feed(self, delta: str) -> list[Say | Draw]:
        out: list[Say | Draw] = []
        self._buf += delta
        while True:
            if not self._in_block:
                i = self._buf.find(FENCE_OPEN)
                if i < 0:
                    held = _held_back(self._buf)
                    safe = self._buf[:len(self._buf) - held]
                    self._buf = self._cut(safe, out, flush=False) + self._buf[len(safe):]
                    break
                self._cut(self._buf[:i], out, flush=True)  # speech before the picture goes first
                self._buf, self._in_block = self._buf[i + len(FENCE_OPEN):], True
            else:
                j = self._buf.find(FENCE)
                if j < 0:
                    break
                code = self._buf[:j].strip()
                if code:
                    out.append(Draw(code))
                self._buf, self._in_block = self._buf[j + len(FENCE):], False
        return out

    def close(self) -> list[Say | Draw]:
        """The text has ended: return whatever is left."""
        out: list[Say | Draw] = []
        if self._in_block:
            if self._buf.strip():
                out.append(Draw(self._buf.strip()))
        else:
            self._cut(self._buf, out, flush=True)
        self._buf, self._in_block = "", False
        return out
