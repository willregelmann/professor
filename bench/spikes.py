"""Stage 0 spikes: how soon is the first word heard when the lecturer lives in the
Claude Agent SDK, and when it can only speak through tools?

    python -m bench.spikes --host claude-sdk --runs 5
    python -m bench.spikes --host tools-only --runs 5
    python -m bench.spikes --host fake --runs 1

Each run is a fresh session given the same three turns as bench.listener, built
from the prototype's own prompts, so the numbers sit beside the Hermes baseline.
The text is cut and spoken the way the prototype does it: one sentence at a time,
through the same speech engine.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import time
from pathlib import Path

from bench import log, metrics
from bench.listener import QUESTION
from professor import speech
from professor.hosts.base import Host
from professor.hosts.fake import FakeHost
from professor.splitter import Draw, Say, Splitter

PROTOTYPE = Path(__file__).resolve().parent.parent / "prototype" / "server.py"


def _prototype():
    spec = importlib.util.spec_from_file_location("prototype_server", PROTOTYPE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prompts() -> list[tuple[str, str]]:
    """The prototype's three turns, word for word as its server builds them."""
    p = _prototype()
    slides = p.DECK["slides"]

    def slide(i: int) -> str:
        s = slides[i]
        text = (f"Present slide {i+1} of {len(slides)}, \"{s['title']}\". "
                f"Speaker notes (cover these in your own words, about {s.get('seconds', 45)} "
                f"seconds of speech, then stop):\n{s['notes']}")
        if s.get("diagram"):
            text += ("\n\nThis diagram is ALREADY on screen for this slide; walk the room "
                     f"through it and do not draw another:\n{s['diagram']}")
        return text

    question = (f"[{QUESTION['who']}] asks, in the meeting chat: {QUESTION['text']}\n\nAnswer out loud, "
                "briefly (under 30 seconds of speech), then stop.")
    return [("slide", p.PREAMBLE + "\n\n" + slide(0)), ("question", question), ("slide-later", slide(1))]


async def deliver(host: Host, prompt: str) -> dict:
    """Run one turn: cut the host's text into sentences and blocks, speak each
    sentence in order, and record when each clip would have reached a listener."""
    t0 = time.monotonic()
    seq: list[list] = []
    clips: list[tuple[float, float]] = []
    first_text = None
    arrivals: list[tuple[float, int]] = []  # when each piece of text came, and how long it was
    queue: asyncio.Queue = asyncio.Queue()

    async def speaker():
        while (item := await queue.get()) is not None:
            if isinstance(item, Draw):
                seq.append(["DRAW", item.code])
                continue
            audio = await speech.speak(item.text)
            if not audio:
                seq.append(["WARN", f"no audio for: {item.text[:80]!r}"])
                continue
            seq.append(["SAY", item.text])
            clips.append((time.monotonic() - t0, speech.duration_s(audio)))

    task = asyncio.create_task(speaker())
    splitter = Splitter()
    async for delta in host.turn(prompt):
        arrivals.append((round(time.monotonic() - t0, 2), len(delta)))
        for item in splitter.feed(delta):
            if first_text is None and isinstance(item, Say):
                first_text = round(time.monotonic() - t0, 2)
            await queue.put(item)
    for item in splitter.close():
        if first_text is None and isinstance(item, Say):
            first_text = round(time.monotonic() - t0, 2)
        await queue.put(item)
    await queue.put(None)
    await task
    return {
        "total_s": round(time.monotonic() - t0, 2),
        "first_text_s": first_text,
        **metrics.playback(clips),
        "sentences": sum(1 for kind, _ in seq if kind == "SAY"),
        "diagrams": sum(1 for kind, _ in seq if kind == "DRAW"),
        "warnings": sum(1 for kind, _ in seq if kind == "WARN"),
        "pieces": len(arrivals),
        "seq": seq,
        "arrivals": arrivals,
    }


def _sdk_options(model: str, effort: str, **extra):
    from claude_agent_sdk import ClaudeAgentOptions
    # No built-in tools, no user or project settings: the lecturer is a bare model,
    # and nothing from this machine's Claude Code setup (hooks, memory, MCP) joins in.
    return ClaudeAgentOptions(model=model, effort=effort, tools=[], setting_sources=[],
                              strict_mcp_config=True, include_partial_messages=True, **extra)


class ClaudeStreamHost:
    """The Agent SDK with partial messages on: text arrives as it's produced."""

    def __init__(self, model: str, effort: str):
        from claude_agent_sdk import ClaudeSDKClient
        self.client = ClaudeSDKClient(options=_sdk_options(model, effort))
        self.session = ""

    async def connect(self) -> None:
        await self.client.connect()

    async def turn(self, prompt: str):
        from claude_agent_sdk import ResultMessage, StreamEvent
        await self.client.query(prompt)
        async for msg in self.client.receive_response():
            if isinstance(msg, StreamEvent):
                delta = msg.event.get("delta", {}) if msg.event.get("type") == "content_block_delta" else {}
                if delta.get("type") == "text_delta":
                    yield delta["text"]
            elif isinstance(msg, ResultMessage):
                self.session = msg.session_id

    async def cancel(self) -> None:
        await self.client.interrupt()

    async def close(self) -> None:
        await self.client.disconnect()


TOOLS_ONLY = (
    "\n\nHOW YOU ARE HEARD. The room hears you ONLY through the say tool. Anything you write outside it "
    "is lost. Call say with one passage at a time, {passage}, and call it the moment each passage is "
    "ready rather than composing the whole talk first. A diagram goes inside the passage it belongs to. "
    "When you have finished the slide or the answer, call listen and wait: it returns the next thing to "
    "do. Never end your turn; always finish by calling listen."
)


class ToolsOnlyHost:
    """A host with tools and nothing else. The agent speaks by calling `say` and
    waits for the next moment by calling `listen`; a passage is heard only once
    its whole tool call has arrived."""

    def __init__(self, model: str, effort: str, passage: str):
        from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server, tool
        self.passage = passage
        self.session = ""
        self.unheard = 0  # characters written outside `say`
        self._said: asyncio.Queue = asyncio.Queue()
        self._next: asyncio.Future | None = None
        self._drain: asyncio.Task | None = None

        @tool("say", "Speak one passage to the room.", {"text": str})
        async def say(args):
            await self._said.put(args["text"])
            return {"content": [{"type": "text", "text": "spoken"}]}

        @tool("listen", "Wait for the next thing to do: a slide to present or a question to answer.", {})
        async def listen(args):
            self._next = asyncio.get_running_loop().create_future()
            await self._said.put(None)  # the turn is over
            return {"content": [{"type": "text", "text": await self._next}]}

        server = create_sdk_mcp_server("lecture", tools=[say, listen])
        self.client = ClaudeSDKClient(options=_sdk_options(
            model, effort, mcp_servers={"lecture": server},
            allowed_tools=["mcp__lecture__say", "mcp__lecture__listen"]))

    async def connect(self) -> None:
        await self.client.connect()

    async def _drain_messages(self) -> None:
        from claude_agent_sdk import ResultMessage, StreamEvent
        async for msg in self.client.receive_messages():
            if isinstance(msg, StreamEvent):
                delta = msg.event.get("delta", {}) if msg.event.get("type") == "content_block_delta" else {}
                if delta.get("type") == "text_delta":
                    self.unheard += len(delta["text"])
            elif isinstance(msg, ResultMessage):
                self.session = msg.session_id
                await self._said.put(None)  # the agent ended its turn without listening

    async def turn(self, prompt: str):
        if self._drain is None:
            await self.client.query(prompt + TOOLS_ONLY.format(passage=self.passage))
            self._drain = asyncio.create_task(self._drain_messages())
        elif self._next is not None and not self._next.done():
            self._next.set_result(prompt)
        else:
            raise RuntimeError("the agent isn't listening: it ended its turn without calling listen")
        while (text := await self._said.get()) is not None:
            yield text + "\n"

    async def cancel(self) -> None:
        await self.client.interrupt()

    async def close(self) -> None:
        if self._drain:
            self._drain.cancel()
        await self.client.disconnect()


FAKE_REPLIES = [
    "Hello. I am a scripted lecturer with nothing behind me, and this first slide is here to be timed. "
    "Each sentence is cut as it arrives and spoken on its own.",
    "Here is how that flows. ```mermaid\nflowchart LR\n  A[\"Lecturer\"] --> B[\"Room\"]\n``` "
    "The lecturer speaks, and the room hears it a sentence at a time.",
    "This is the second slide. It exists so that a later turn in the same session can be measured.",
]


async def make_host(name: str, args) -> tuple[Host, float]:
    t0 = time.monotonic()
    if name == "fake":
        return FakeHost(FAKE_REPLIES), 0.0
    if name == "claude-sdk":
        host = ClaudeStreamHost(args.model, args.effort)
    elif name == "tools-only":
        host = ToolsOnlyHost(args.model, args.effort, args.passage)
    else:
        raise SystemExit(f"unknown host {name!r}")
    await host.connect()
    return host, round(time.monotonic() - t0, 2)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", required=True, choices=["fake", "claude-sdk", "tools-only"])
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--passage", default="two or three sentences",
                    help="tools-only: how much the agent is told to put in each say call")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    run = log.run_id()
    for n in range(args.runs):
        host, connect_s = await make_host(args.host, args)
        turns = []
        try:
            for name, prompt in prompts():
                t = await deliver(host, prompt)
                turns.append(t)
                config = {"effort": args.effort, "connect_s": connect_s}
                if args.host == "tools-only":
                    config["passage"] = args.passage
                    t["unheard_chars"] = host.unheard
                log.record({"kind": "latency", "host": args.host, "path": "spike",
                            "model": None if args.host == "fake" else args.model, "config": config,
                            "turn": name, "run": run, "n": n,
                            "metrics": {k: v for k, v in t.items() if k not in ("seq", "arrivals")}})
                print(f"run {n} {name:12} first text {t['first_text_s']}s  first audio {t['first_audio_s']}s  "
                      f"total {t['total_s']}s  dead air {t['dead_air_s']}s (longest {t['longest_gap_s']}s)  "
                      f"{t['sentences']} sentences  {t['diagrams']} diagrams  {t['warnings']} warnings",
                      flush=True)
        finally:
            log.save_transcript(run, f"{args.host}-spike-{n}", turns)
            if hasattr(host, "close"):
                await host.close()


if __name__ == "__main__":
    asyncio.run(main())
