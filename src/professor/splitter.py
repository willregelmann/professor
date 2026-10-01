"""Separates what's said from what isn't, in order, as the agent's text arrives.

The agent writes plain spoken prose. Anything that isn't speech goes inside a
fenced block whose first line says what it is: a diagram to draw (```mermaid),
or a mark that tells the lecture something (```later, ```decline). `feed` takes
text as it's produced and returns whatever is now complete: sentences to speak,
diagrams to draw and marks to act on, in the order they were written.

A block is never spoken, whatever kind it is. A kind the lecture doesn't know
comes back as a mark and is simply not said.

Carried over from the prototype (prototype/server.py), with its lessons:
- a sentence under MIN_SENTENCE characters is merged with the next, because a
  short opener ("Hello.") once held a whole reply back;
- text that might be the start of a fence is held until it's known to be speech
  or a block, so a partial fence is never spoken;
- an unterminated block is still acted on, never spoken.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FENCE = "```"
DRAWN = "mermaid"
MIN_SENTENCE = 20
SENTENCE_END = re.compile(r"[.!?](?=\s)")


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Draw:
    code: str


@dataclass(frozen=True)
class Mark:
    kind: str
    body: str


def _speakable(text: str) -> str:
    """Never read markup aloud: a stray backtick is noise to a listener."""
    return text.replace("`", "").strip()


def _held_back(text: str) -> int:
    """How many trailing characters could still turn out to be the start of a fence."""
    for n in range(len(FENCE) - 1, 0, -1):
        if text.endswith(FENCE[:n]):
            return n
    return 0


def _block(text: str) -> Draw | Mark | None:
    """A block's text, from just after its opening fence: its kind, then its body."""
    kind, _, body = text.partition("\n")
    kind, body = kind.strip().lower(), body.strip()
    if kind == DRAWN:
        return Draw(body) if body else None
    return Mark(kind, body)


class Splitter:
    def __init__(self) -> None:
        self._buf = ""
        self._in_block = False

    def _cut(self, text: str, out: list, *, flush: bool) -> str:
        start = 0
        for m in SENTENCE_END.finditer(text):
            if len(text[start:m.end()].strip()) >= MIN_SENTENCE:
                out.append(Say(_speakable(text[start:m.end()])))
                start = m.end()
        rest = text[start:]
        if flush and _speakable(rest):
            out.append(Say(_speakable(rest)))
        return "" if flush else rest

    def feed(self, delta: str) -> list[Say | Draw | Mark]:
        out: list[Say | Draw | Mark] = []
        self._buf += delta
        while True:
            if not self._in_block:
                i = self._buf.find(FENCE)
                if i < 0:
                    held = _held_back(self._buf)
                    safe = self._buf[:len(self._buf) - held]
                    self._buf = self._cut(safe, out, flush=False) + self._buf[len(safe):]
                    break
                self._cut(self._buf[:i], out, flush=True)  # speech before the block goes first
                self._buf, self._in_block = self._buf[i + len(FENCE):], True
            else:
                j = self._buf.find(FENCE)
                if j < 0:
                    break
                if (item := _block(self._buf[:j])) is not None:
                    out.append(item)
                self._buf, self._in_block = self._buf[j + len(FENCE):], False
        return out

    def close(self) -> list[Say | Draw | Mark]:
        """The text has ended: return whatever is left."""
        out: list[Say | Draw | Mark] = []
        if self._in_block:
            if (item := _block(self._buf)) is not None:
                out.append(item)
        else:
            self._cut(self._buf, out, flush=True)
        self._buf, self._in_block = "", False
        return out

    def unsaid(self) -> str:
        """Speech that has arrived but hasn't made a whole sentence yet."""
        return "" if self._in_block else _speakable(self._buf)
