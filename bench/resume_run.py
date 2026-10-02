"""Stage 6's exit run: a lecture started by one lecturer is picked up by another.

    HERMES_API_KEY=... python -m bench.resume_run --hermes-url http://ha-pi.local:8642

A Hermes agent starts the three-slide deck about Professor. A listener asks
something the last slide covers, and the server is taken down partway through the
second slide. The stopped lecture is then picked up twice, from two copies of the
store, by Claude through the Agent SDK:

- once as if a month had passed, with a listener asking it to repeat a story that
  was never told;
- once after the real gap of a few seconds.

What was said is written to .claude/tests/resume-behaviour/ for `/test:run resume-behaviour`.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestServer

from bench import lecture_run, listener, log
from professor import importers
from professor.hosts import hermes
from professor.hosts.claude_sdk import ClaudeHost
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import SETTLED, Store

TEST_DIR = lecture_run.ROOT / ".claude" / "tests" / "resume-behaviour"
LATER = ("Marcus", "What happens if the speech engine fails halfway through a sentence?")
NEVER_TOLD = ("Dana", "Before we stopped you told a story about the first lecture you ever gave. "
                      "Can you tell it again?")
MONTH = 30 * 86400


def said(store: Store, lecture_id: int, after_utterance: int = 0) -> str:
    """What was said, slide by slide and question by question, as the lecture kept it."""
    title = {s.id: s.title for s in store.main_line(store.lecture(lecture_id)["deck_id"])}
    visits = {v["visit_id"]: v for v in store.path(lecture_id)}
    questions = {q.id: q for q in store.questions(lecture_id)}
    out, last = [], None
    for u in store.utterances(lecture_id):
        if u.id <= after_utterance or u.kind != "said" or u.failed not in (None, "cut off"):
            continue
        key = ("q", u.question_id) if u.question_id else ("s", u.visit_id)
        if key != last:
            if u.question_id:
                q = questions[u.question_id]
                out += ["", f"## In reply to {q.asker or 'someone'}, who asked: \"{q.text}\"", ""]
            else:
                v = visits[u.visit_id]
                out += ["", f"## On slide \"{title.get(v['slide_id'])}\"", ""]
            last = key
        out.append(u.text)
    return "\n".join(out)


async def give(store: Store, lecture_id: int, host, on_event, **options) -> list[dict]:
    server = TestServer(make_app(Lecture(store, lecture_id, host, **options)))
    await server.start_server()
    try:
        return (await listener.attend(str(server.make_url("/ws")), on_event=on_event, timeout_s=1800))["events"]
    finally:
        await server.close()  # the server goes away; the lecture is not ended


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hermes-url", default=os.environ.get("HERMES_URL", "http://127.0.0.1:8642"))
    ap.add_argument("--model", default="claude-opus-5-5")
    args = ap.parse_args()
    run = log.run_id()
    spec = lecture_run.decks()["parity"]
    work = Path(tempfile.mkdtemp(prefix="professor-resume-"))

    # The first part: Wren, until the server goes down partway through slide 2.
    store = Store(work / "first.db")
    deck = importers.import_prototype_deck(store, spec["path"])
    lecture_id = store.create_lecture(deck.id, "hermes")
    state = {"slide": -1, "sentences": 0, "taking": False, "asked": False}

    async def ask_then_leave(ws, event):
        if event["type"] == "slide":
            state["slide"], state["sentences"] = event["place"]["index"], 0
        elif event["type"] == "question":
            state["taking"] = event["question"]["state"] == "answering"
        elif event["type"] == "sentence" and not state["taking"]:
            state["sentences"] += 1
            if state["slide"] == 0 and state["sentences"] == 2 and not state["asked"]:
                state["asked"] = True
                await ws.send_json({"cmd": "ask", "who": LATER[0], "text": LATER[1]})
            if state["slide"] == 1 and state["sentences"] == 3:
                await asyncio.sleep(event["seconds"] / 2)
                return "leave"

    await give(store, lecture_id, hermes.HermesHost(args.hermes_url, hermes.find_key()), ask_then_leave)
    first = said(store, lecture_id)
    kept_until = max((u.id for u in store.utterances(lecture_id)), default=0)
    at_stop = {"path": [(v["how"], v["covered"]) for v in store.path(lecture_id)],
               "questions": [(q.text, q.state) for q in store.questions(lecture_id)]}
    store.close()

    results = {"at_stop": at_stop}
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "first-part.md").write_text("# The first part of the lecture, before it stopped\n\n"
                                            "This is everything the lecture kept of it.\n" + first)

    for name, gap_s in (("short", None), ("long", MONTH)):  # the short one first, while the gap is still short
        shutil.copy(work / "first.db", work / f"{name}.db")
        store = Store(work / f"{name}.db")
        store.set_host(lecture_id, "claude")
        asked = False

        async def trap(ws, event):
            nonlocal asked
            # In the long-gap lecture, ask for something that was never said.
            if name == "long" and not asked and event["type"] == "slide" and event["place"]["index"] == 2:
                asked = True
                await ws.send_json({"cmd": "ask", "who": NEVER_TOLD[0], "text": NEVER_TOLD[1]})

        events = await give(store, lecture_id, ClaudeHost(model=args.model), trap, resume_gap_s=gap_s)
        log.save_transcript(run, f"resume-{name}", events)
        (TEST_DIR / f"resumed-after-a-{name}-gap.md").write_text(
            f"# The lecture after it was picked up by a different lecturer, {'a month' if gap_s else 'seconds'} later\n"
            + said(store, lecture_id, after_utterance=kept_until))
        path = store.path(lecture_id)
        results[name] = {
            "resumed_at_slide": next(e["place"]["index"] + 1 for e in events if e["type"] == "slide"),
            "path_after": [(v["how"], v["covered"]) for v in path[len(at_stop["path"]):]],
            "first_slide_given_again": any(v["how"] != "start" and v["slide_id"] == path[0]["slide_id"]
                                           for v in path[1:]),
            "questions": [(q.text[:40], q.state) for q in store.questions(lecture_id)],
            "all_settled": all(q.state in SETTLED for q in store.questions(lecture_id)),
            "put_off_came_back": [s for s, _ in listener.questions(events)[0]["states"]]
            if listener.questions(events) else [],
        }
        store.close()

    log.record({"kind": "resume", "host": "hermes>claude", "model": args.model, "run": run, "metrics": results})
    for key, value in results.items():
        print(key, value, flush=True)
    print(f"wrote {TEST_DIR}/first-part.md, resumed-after-a-long-gap.md and resumed-after-a-short-gap.md")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
