"""Stage 4's exit run: a real lecturer is asked to move the lecture and to make
a slide, and where the lecture went is recorded.

    HERMES_API_KEY=... python -m bench.navigate_run --hermes-url http://ha-pi.local:8642 --runs 2

Each run is a fresh lecture of the prototype's deck. A scripted listener:
- stops the lecturer on the first slide and asks it to skip the rest of it;
- asks for a sketch, which should become a slide made up on the spot;
- asks it to go back to an earlier slide's diagram, which should be a detour.

It writes what was said and where the screen went to
.claude/tests/moves-are-announced/transcript.md, for `/test:run moves-are-announced`.
"""
from __future__ import annotations

import argparse
import asyncio
import os

from aiohttp.test_utils import TestServer

from bench import lecture_run, listener, log, metrics, spikes
from professor import importers
from professor.hosts import hermes
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import SETTLED, Store

TEST_DIR = spikes.PROTOTYPE.parent.parent / ".claude" / "tests" / "moves-are-announced"
SKIP = ("Dana", "I already know who you are. Could you skip the rest of this slide?")
SKETCH = ("Priya", "Could you sketch how a message gets from you to Ash and back?")
GO_BACK = ("Marcus", "Can you go back to the memory diagram and point out where the search by meaning happens?")


class Listener:
    def __init__(self):
        self.slide, self.sentences, self.taking, self.done = -1, 0, False, set()

    async def __call__(self, ws, event) -> None:
        if event["type"] == "slide" and not event["place"]["detour"]:
            self.slide, self.sentences = event["place"]["index"], 0
        elif event["type"] == "question":
            self.taking = event["question"]["state"] == "answering"
        elif event["type"] == "stopped" and "skip" not in self.done:
            self.done.add("skip")
            await ws.send_json({"cmd": "ask", "who": SKIP[0], "text": SKIP[1]})
        elif event["type"] == "sentence" and not self.taking:
            self.sentences += 1
            at = (self.slide, self.sentences)
            if at == (0, 2) and "stop" not in self.done:
                self.done.add("stop")
                await asyncio.sleep(min(1.0, event["seconds"] / 2))
                await ws.send_json({"cmd": "stop"})
            elif at == (1, 2) and "sketch" not in self.done:
                self.done.add("sketch")
                await ws.send_json({"cmd": "ask", "who": SKETCH[0], "text": SKETCH[1]})
            elif at == (2, 1) and "back" not in self.done:
                self.done.add("back")
                await ws.send_json({"cmd": "ask", "who": GO_BACK[0], "text": GO_BACK[1]})


def _where(place: dict, titles: list[str]) -> str:
    if place.get("made_up"):
        return f"a slide made up on the spot (a detour from slide {place['from'] + 1})"
    where = f"slide {place['index'] + 1}, \"{titles[place['index']]}\""
    return where + (f" (a detour from slide {place['from'] + 1})" if place["detour"] else "")


def transcript(events: list[dict], titles: list[str]) -> str:
    out = ["# A lecture: what was said, and where the screen went", ""]
    for e in events:
        if e["type"] == "slide":
            made = f": \"{e['slide']['title']}\"" if e["place"].get("made_up") else ""
            out += ["", f"**[The screen now shows {_where(e['place'], titles)}{made}]**", ""]
        elif e["type"] == "sentence":
            out.append(e["text"])
        elif e["type"] == "question" and e["question"]["state"] == "heard":
            q = e["question"]
            out += ["", f"*[{q['who'] or 'Someone'} asks in the chat: \"{q['text']}\"]*", ""]
        elif e["type"] == "question" and e["question"]["state"] == "answering":
            out += ["", f"*[The lecturer takes the question from {e['question']['who'] or 'someone'}]*", ""]
        elif e["type"] == "stopped":
            out += ["", "*[A listener stops the lecturer]*", ""]
    return "\n".join(out)


async def one_run(args, n: int, run: str) -> dict:
    store = Store()
    deck = importers.import_prototype_deck(store, spikes.PROTOTYPE.parent / "deck.json",
                                           audience="a room of software engineers",
                                           background=lecture_run.background())
    before = [s.id for s in store.main_line(deck.id)]
    titles = [s.title for s in store.main_line(deck.id)]
    host = hermes.HermesHost(args.hermes_url, hermes.find_key())
    lecture_id = store.create_lecture(deck.id, "hermes")
    server = TestServer(make_app(Lecture(store, lecture_id, host)))
    await server.start_server()
    try:
        record = await listener.attend(str(server.make_url("/ws")), on_event=Listener(), timeout_s=1800)
    finally:
        await server.close()
        await host.close()
    events = record["events"]
    log.save_transcript(run, f"hermes-navigate-{n}", record)
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "transcript.md").write_text(transcript(events, titles))

    places = [("made up" if e["place"].get("made_up") else e["place"]["index"] + 1, e["place"]["detour"])
              for e in events if e["type"] == "slide"]
    made = store.beside(deck.id)
    result = {
        "places": places,
        "path": [(v["how"], v["covered"]) for v in store.path(lecture_id)],
        "main_line_unchanged": [s.id for s in store.main_line(deck.id)] == before,
        "first_slide_skipped": not store.path(lecture_id)[0]["covered"],
        "detours_to_slides": sum(1 for where, depth in places if depth and where != "made up"),
        "made_up": [{"title": s.title, "checked": s.show_checked, "has_diagram": bool(s.diagram),
                     "for_question": bool(s.question_id)} for s in made],
        "questions": [{"text": q.text[:50], "state": q.state} for q in store.questions(lecture_id)],
        "all_settled": all(q.state in SETTLED for q in store.questions(lecture_id)),
        "warnings": [e["message"] for e in events if e["type"] == "warn"],
        "unexplained": metrics.unexplained(events),
        "spoken_s": round(sum(e["seconds"] for e in events if e["type"] == "sentence")),
    }
    log.record({"kind": "navigate", "host": "hermes", "model": args.model, "run": run, "n": n, "metrics": result})
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hermes-url", default=os.environ.get("HERMES_URL", "http://127.0.0.1:8642"))
    ap.add_argument("--model", default=None, help="the model behind the host, for the log")
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()
    run = log.run_id()
    for n in range(args.runs):
        r = await one_run(args, n, run)
        print(f"run {n} places {r['places']}", flush=True)
        print(f"run {n} path {r['path']}", flush=True)
        print(f"run {n} made up {r['made_up']}", flush=True)
        print(f"run {n} questions {[(q['state']) for q in r['questions']]}  warnings {r['warnings']}", flush=True)
        print(f"run {n} done: main line unchanged {r['main_line_unchanged']}  first slide skipped "
              f"{r['first_slide_skipped']}  detours {r['detours_to_slides']}  all settled {r['all_settled']}  "
              f"unexplained {len(r['unexplained'])}  spoken {r['spoken_s']}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
