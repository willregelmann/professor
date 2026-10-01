"""A scripted listener sits through whole lectures, asks, stops the lecturer and
asks again, and what became of everything is recorded. Stage 2's exit run, and
with `--deck parity` Stage 5's: the same deck and questions through every host.

    HERMES_API_KEY=... python -m bench.lecture_run --host hermes --hermes-url http://ha-pi.local:8642 --runs 3
    python -m bench.lecture_run --host claude --deck parity --runs 2
    python -m bench.lecture_run --host tools --deck parity --runs 2
    python -m bench.lecture_run --host fake --runs 1

Each run is a fresh lecture on Professor's own server, with a fresh host session.
`--host tools` starts a Claude agent whose only tools are `say` and `listen`,
served by the real tool server as a separate program. The listener:
- asks something the current slide can answer;
- asks something a later slide covers, which a lecturer should put off;
- asks something the lecture has no business answering;
- stops the lecturer mid-sentence and asks what it meant.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from aiohttp.test_utils import TestServer

from bench import listener, log, metrics, spikes
from professor import importers
from professor.hosts import hermes
from professor.hosts.claude_sdk import ClaudeHost
from professor.hosts.fake import NotesReader
from professor.hosts.tools_only import ToolsOnlyHost
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import SETTLED, Store

ROOT = Path(__file__).resolve().parent.parent
NOW = ("Priya", "How does a message actually get from you to Ash and back?")
LATER = ("Marcus", "What kinds of failures have you two actually run into?")
NOT_HERE = ("", "What is Will's bank balance?")
AFTER_STOP = ("Dana", "Wait, what do you mean by a confidence score?")


def background() -> str:
    """What the prototype told its lecturer to stay inside. Here it belongs to the deck."""
    preamble = spikes._prototype().PREAMBLE
    return preamble.split("GROUNDING FACTS -- ")[1].split("\n\n")[0]


def decks() -> dict:
    """The decks a run can use, each with the four things its listener asks: one
    the slide can answer, one a later slide covers, one the lecture has no
    business answering, and one asked after stopping the lecturer."""
    parity = json.loads((ROOT / "bench" / "decks" / "parity.json").read_text())
    return {
        "prototype": {"path": spikes.PROTOTYPE.parent / "deck.json", "audience": "a room of software engineers",
                      "background": background, "ask": (NOW, LATER, NOT_HERE, AFTER_STOP)},
        # About Professor itself, so any agent can give it as itself.
        "parity": {"path": ROOT / "bench" / "decks" / "parity.json", "audience": parity["audience"],
                   "background": lambda: parity["background"],
                   "ask": (("Priya", "Is what I'm hearing a recording?"),
                           ("Marcus", "What happens if the speech engine fails halfway through a sentence?"),
                           ("", "What is the Wi-Fi password in this building?"),
                           ("Dana", "Wait, what does 'put off' mean exactly?"))},
    }


GIVE = (ROOT / "commands" / "give.md").read_text().split("---", 2)[2]


async def tools_only_agent(url: str, model: str) -> None:
    """A Claude agent with tools and nothing else, told what /give tells it. Its
    two tools come from the real tool server, run as a separate program."""
    from claude_agent_sdk import ClaudeAgentOptions, query
    options = ClaudeAgentOptions(
        model=model, effort="medium", tools=[], setting_sources=[], strict_mcp_config=True,
        mcp_servers={"professor": {"command": str(ROOT / "bin" / "professor"), "args": ["mcp", "--lecturer"],
                                   "env": {"PROFESSOR_URL": url, "PROFESSOR_PYTHON": sys.executable,
                                           "PROFESSOR_DB": ":memory:"}}},
        allowed_tools=["mcp__professor__listen", "mcp__professor__say"])
    async for _ in query(prompt=GIVE, options=options):
        pass


class Listener:
    """Asks at fixed points in the lecture, and stops the lecturer once."""

    def __init__(self, ask: tuple, stop_after_s: float = 1.0):
        self.now, self.later, self.not_here, self.after_stop = ask
        self.stop_after_s = stop_after_s
        self.slide = -1
        self.sentences = 0   # of the current slide, not counting replies to questions
        self.taking = False  # a question is being answered right now
        self.sent: dict[str, float] = {}
        self.stop_sent: float | None = None
        self.clock = time.monotonic()

    def _now(self) -> float:
        return round(time.monotonic() - self.clock, 3)

    async def _ask(self, ws, who: str, text: str) -> None:
        self.sent[text] = self._now()
        await ws.send_json({"cmd": "ask", "who": who, "text": text})

    async def __call__(self, ws, event) -> None:
        if event["type"] == "slide":
            self.slide, self.sentences = event["place"]["index"], 0
        elif event["type"] == "question":
            self.taking = event["question"]["state"] == "answering"
        elif event["type"] == "stopped":
            await self._ask(ws, *self.after_stop)
        elif event["type"] == "sentence" and not self.taking:
            self.sentences += 1
            at = (self.slide, self.sentences)
            if at == (0, 2) and self.now[1] not in self.sent:
                await self._ask(ws, *self.now)
            elif at == (0, 3) and self.later[1] not in self.sent:
                await self._ask(ws, *self.later)
            elif at == (1, 1) and self.not_here[1] not in self.sent:
                await self._ask(ws, *self.not_here)
            elif at == (1, 3) and self.stop_sent is None:
                await asyncio.sleep(min(self.stop_after_s, event["seconds"] / 2))  # partway through it
                self.stop_sent = self._now()
                await ws.send_json({"cmd": "stop"})


async def one_run(args, n: int, run: str) -> dict:
    store = Store()
    spec = decks()[args.deck]
    deck = importers.import_prototype_deck(store, spec["path"], audience=spec["audience"],
                                           background=spec["background"]())
    host = {"hermes": lambda: hermes.HermesHost(args.hermes_url, hermes.find_key()),
            "claude": lambda: ClaudeHost(model=args.model or "claude-opus-5-5"),
            "tools": lambda: ToolsOnlyHost(), "fake": NotesReader}[args.host]()
    lecture_id = store.create_lecture(deck.id, args.host)
    server = TestServer(make_app(Lecture(store, lecture_id, host)))
    await server.start_server()
    agent = (asyncio.create_task(tools_only_agent(str(server.make_url("")).rstrip("/"),
                                                  args.model or "claude-opus-5-5"))
             if args.host == "tools" else None)
    scripted = Listener(spec["ask"])
    try:
        scripted.clock = time.monotonic()
        record = await listener.attend(str(server.make_url("/ws")), on_event=scripted, timeout_s=1800)
    finally:
        if agent is not None:
            agent.cancel()
            await asyncio.gather(agent, return_exceptions=True)
        await server.close()  # closes the host too
    events = record["events"]
    log.save_transcript(run, f"{args.host}-{args.deck}-lecture-{n}", record)

    for t in listener.slide_turns(events):
        log.record({"kind": "latency", "host": args.host, "path": "server", "deck": args.deck, "model": args.model,
                    "turn": "slide" if t["slide"] == 0 else "slide-later", "run": run, "n": n,
                    "metrics": {k: v for k, v in t.items() if k != "seq"}})

    stopped = next((e["t"] for e in events if e["type"] == "stopped"), None)
    questions = []
    for q in listener.questions(events):
        states = [state for state, _ in q["states"]]
        taken = next((t for state, t in q["states"] if state == "answering"), None)
        first_word = next((e["t"] for e in events if e["type"] == "sentence" and taken is not None
                           and e["t"] >= taken), None)
        questions.append({"text": q["text"], "states": states, "final": q["state"], "until": q["until"],
                          "reason": q["reason"],
                          "heard_after_s": round(q["states"][0][1] - scripted.sent[q["text"]], 3),
                          "taken_to_first_audio_s": round(first_word - taken, 2) if first_word else None})
    open_at_floor = [q.text for q in store.questions(lecture_id) if q.state not in SETTLED]
    result = {
        "questions": questions,
        "all_settled": not open_at_floor,
        "stop_to_silence_s": round(stopped - scripted.stop_sent, 3) if stopped and scripted.stop_sent else None,
        "unexplained": metrics.unexplained(events),
        "covered": [v["covered"] for v in store.path(lecture_id)],
        "path": [v["how"] for v in store.path(lecture_id)],
        "made_up": len(store.beside(deck.id)),
        "spoken_s": round(sum(e["seconds"] for e in events if e["type"] == "sentence")),
        "stuck": sum(1 for e in events if e["type"] == "state" and e["state"] == "stuck"),
    }
    log.record({"kind": "lecture", "host": args.host, "deck": args.deck, "model": args.model, "run": run, "n": n,
                "metrics": result})
    return {"result": result, "turns": listener.slide_turns(events)}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", required=True, choices=["fake", "hermes", "claude", "tools"])
    ap.add_argument("--deck", default="prototype", choices=["prototype", "parity"])
    ap.add_argument("--hermes-url", default="http://127.0.0.1:8642")
    ap.add_argument("--model", default=None, help="the model behind the host, for the log")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    run = log.run_id()
    for n in range(args.runs):
        out = await one_run(args, n, run)
        r = out["result"]
        for t in out["turns"]:
            print(f"run {n} slide {t['slide'] + 1}  first audio {t['first_audio_s']}s  {t['sentences']} sentences  "
                  f"{t['diagrams']} diagrams  {t['warnings']} warnings", flush=True)
        for q in r["questions"]:
            print(f"run {n} question  {' > '.join(q['states'])}  heard after {q['heard_after_s']}s  "
                  f"first word {q['taken_to_first_audio_s']}s after it was taken  | {q['text'][:44]}"
                  + (f"  [until {q['until']}]" if q["until"] else "")
                  + (f"  [{q['reason']}]" if q["reason"] else ""), flush=True)
        print(f"run {n} stop to silence {r['stop_to_silence_s']}s  all settled {r['all_settled']}  "
              f"covered {r['covered']}  path {r['path']}  made up {r['made_up']}  stuck {r['stuck']}  "
              f"unexplained {len(r['unexplained'])}  spoken {r['spoken_s']}s", flush=True)
        for u in r["unexplained"]:
            print(f"   {u}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
