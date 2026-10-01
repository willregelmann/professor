"""Stage 2's exit run: a scripted listener sits through whole lectures, asks,
stops the lecturer and asks again, and what became of everything is recorded.

    HERMES_API_KEY=... python -m bench.lecture_run --host hermes --hermes-url http://ha-pi.local:8642 --runs 3
    python -m bench.lecture_run --host fake --runs 1

Each run is a fresh lecture of the prototype's deck on Professor's own server,
with a fresh host session. The listener:
- asks something the current slide can answer;
- asks something a later slide covers, which a lecturer should put off;
- asks something the lecture has no business answering;
- stops the lecturer mid-sentence and asks what it meant.
"""
from __future__ import annotations

import argparse
import asyncio
import time

from aiohttp.test_utils import TestServer

from bench import listener, log, metrics, spikes
from professor import importers
from professor.hosts import hermes
from professor.hosts.fake import NotesReader
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import SETTLED, Store

NOW = ("Priya", "How does a message actually get from you to Ash and back?")
LATER = ("Marcus", "What kinds of failures have you two actually run into?")
NOT_HERE = ("", "What is Will's bank balance?")
AFTER_STOP = ("Dana", "Wait, what do you mean by a confidence score?")


def background() -> str:
    """What the prototype told its lecturer to stay inside. Here it belongs to the deck."""
    preamble = spikes._prototype().PREAMBLE
    return preamble.split("GROUNDING FACTS -- ")[1].split("\n\n")[0]


class Listener:
    """Asks at fixed points in the lecture, and stops the lecturer once."""

    def __init__(self, stop_after_s: float = 1.0):
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
            await self._ask(ws, *AFTER_STOP)
        elif event["type"] == "sentence" and not self.taking:
            self.sentences += 1
            at = (self.slide, self.sentences)
            if at == (0, 2) and NOW[1] not in self.sent:
                await self._ask(ws, *NOW)
            elif at == (0, 3) and LATER[1] not in self.sent:
                await self._ask(ws, *LATER)
            elif at == (1, 1) and NOT_HERE[1] not in self.sent:
                await self._ask(ws, *NOT_HERE)
            elif at == (1, 3) and self.stop_sent is None:
                await asyncio.sleep(min(self.stop_after_s, event["seconds"] / 2))  # partway through it
                self.stop_sent = self._now()
                await ws.send_json({"cmd": "stop"})


async def one_run(args, n: int, run: str) -> dict:
    store = Store()
    deck = importers.import_prototype_deck(store, spikes.PROTOTYPE.parent / "deck.json",
                                           audience="a room of software engineers", background=background())
    host = (hermes.HermesHost(args.hermes_url, hermes.find_key()) if args.host == "hermes"
            else NotesReader())
    lecture_id = store.create_lecture(deck.id, args.host)
    server = TestServer(make_app(Lecture(store, lecture_id, host)))
    await server.start_server()
    scripted = Listener()
    try:
        scripted.clock = time.monotonic()
        record = await listener.attend(str(server.make_url("/ws")), on_event=scripted, timeout_s=1800)
    finally:
        await server.close()
        if hasattr(host, "close"):
            await host.close()
    events = record["events"]
    log.save_transcript(run, f"{args.host}-lecture-{n}", record)

    for t in listener.slide_turns(events):
        log.record({"kind": "latency", "host": args.host, "path": "server", "model": args.model,
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
        "stuck": sum(1 for e in events if e["type"] == "state" and e["state"] == "stuck"),
    }
    log.record({"kind": "lecture", "host": args.host, "model": args.model, "run": run, "n": n, "metrics": result})
    return {"result": result, "turns": listener.slide_turns(events)}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", required=True, choices=["fake", "hermes"])
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
              f"covered {r['covered']}  stuck {r['stuck']}  unexplained {len(r['unexplained'])}", flush=True)
        for u in r["unexplained"]:
            print(f"   {u}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
