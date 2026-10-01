"""Stage 3's exit run, in two halves.

    python -m bench.prepared_run prepare --repo . --db bench/data/prepared.db
    HERMES_API_KEY=... python -m bench.prepared_run give --db bench/data/prepared.db --deck 1 \
        --hermes-url http://ha-pi.local:8642

`prepare` has Claude Code, with the professor plugin loaded, prepare a deck that
explains a repository. `give` has a Hermes agent, which can't see that repository,
give the deck while a scripted listener asks one question the deck can answer and
one it can't. It then writes the deck and what was said side by side under
.claude/tests/deck-stands-alone/, where `/test:run deck-stands-alone` judges
whether anything was invented beyond the notes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from aiohttp.test_utils import TestServer

from bench import listener, log, metrics
from professor.hosts import hermes
from professor.hosts.fake import NotesReader
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import SETTLED, Store

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / ".claude" / "tests" / "deck-stands-alone"
TOOL = "mcp__plugin_professor_professor__"
REQUEST = ("Prepare a five-minute lecture that explains this codebase to an engineer who is new to it. "
           "Use the preparing-a-deck skill and the professor tools. When the deck is ready, reply with "
           "its number and its slide titles.")
IN_THE_DECK = ("Priya", "What happens to a question if the lecturer never says what became of it?")
BEYOND_THE_DECK = ("Marcus", "How many people use Professor in production today, and at which companies?")


async def prepare(args) -> None:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, ToolResultBlock,
                                  ToolUseBlock, UserMessage, query)
    db = Path(args.db).resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        model=args.model, cwd=str(Path(args.repo).resolve()), setting_sources=[],
        plugins=[{"type": "local", "path": str(ROOT)}],
        tools=["Read", "Glob", "Grep", "Skill"],
        allowed_tools=["Read", "Glob", "Grep", "Skill"] + [TOOL + name for name in (
            "deck", "slide", "lecture", "questions", "describe", "write_slide", "arrange")],
        env={"PROFESSOR_PYTHON": sys.executable, "PROFESSOR_DB": str(db)})
    t0 = time.monotonic()
    calls: list[dict] = []
    pending: dict[str, dict] = {}
    final, cost = "", None
    async for msg in query(prompt=REQUEST, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    call = {"tool": block.name.removeprefix(TOOL), "input": block.input}
                    pending[block.id] = call
                    calls.append(call)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in pending:
                    pending[block.tool_use_id]["failed"] = bool(block.is_error)
        elif isinstance(msg, ResultMessage):
            final, cost = msg.result or "", msg.total_cost_usd

    store = Store(db)
    decks = store.decks()
    writes = [c for c in calls if c["tool"] == "write_slide"]
    slides = store.main_line(decks[-1].id) if decks else []
    result = {
        "seconds": round(time.monotonic() - t0), "cost_usd": cost,
        "tool_calls": {name: sum(1 for c in calls if c["tool"] == name) for name in sorted({c["tool"] for c in calls})},
        "slides_written": len(writes), "slides_refused_by_the_check": sum(1 for c in writes if c.get("failed")),
        "deck": decks[-1].id if decks else None, "slides": len(slides),
        "with_diagram": sum(1 for s in slides if s.diagram), "with_code": sum(1 for s in slides if s.code),
        "with_sources": sum(1 for s in slides if s.sources),
        "checked": sum(1 for s in slides if s.show_checked), "cant_be_shown": sum(1 for s in slides if s.show_error),
        "planned_seconds": sum(s.seconds or 0 for s in slides),
    }
    run = log.run_id()
    log.save_transcript(run, "prepare", {"calls": calls, "final": final})
    log.record({"kind": "prepare", "host": "claude-code", "model": args.model, "run": run, "metrics": result})
    print(json.dumps(result, indent=1))
    print(final)


def _deck_markdown(store: Store, deck_id: int) -> str:
    deck = store.deck(deck_id)
    out = [f"# The deck: {deck.subject}", "", f"For: {deck.audience}", "", "## Background", "", deck.background, ""]
    for i, s in enumerate(store.main_line(deck_id)):
        out += [f"## Slide {i + 1}: {s.title}", "", "### Notes", "", s.notes, ""]
        for name in ("text", "diagram", "code", "math"):
            if getattr(s, name):
                out += [f"### Shown: {name}", "", "```", getattr(s, name), "```", ""]
        if s.sources:
            out += ["### Sources shown", "", *[f"- {src}" for src in s.sources], ""]
    return "\n".join(out)


def _transcript_markdown(store: Store, lecture_id: int, events: list[dict]) -> str:
    out = ["# What the lecturer said", ""]
    title = {s.id: s.title for s in store.main_line(store.lecture(lecture_id)["deck_id"])}
    questions = {q.id: q for q in store.questions(lecture_id)}
    visits = {v["visit_id"]: v["slide_id"] for v in store.path(lecture_id)}
    last = None
    for u in store.utterances(lecture_id):
        key = ("q", u.question_id) if u.question_id else ("s", u.visit_id)
        if key != last:
            if u.question_id:
                q = questions[u.question_id]
                out += ["", f"## In reply to {q.asker or 'someone'}, who asked: \"{q.text}\" "
                            f"(this question ended: {q.state})", ""]
            else:
                out += ["", f"## Presenting the slide: {title.get(visits[u.visit_id])}", ""]
            last = key
        if u.kind == "said" and u.failed in (None, "cut off"):
            out.append(u.text)
        elif u.kind == "drawn":
            out += ["", "(drew a diagram)", "```", u.text, "```", ""]
    return "\n".join(out)


async def give(args) -> None:
    store = Store(args.db)
    deck = store.deck(args.deck)
    host = hermes.HermesHost(args.hermes_url, hermes.find_key()) if args.host == "hermes" else NotesReader()
    lecture_id = store.create_lecture(deck.id, args.host)
    server = TestServer(make_app(Lecture(store, lecture_id, host)))
    await server.start_server()
    asked: set[str] = set()

    async def ask(ws, event) -> None:
        # One question during the second slide, one during the third.
        if event["type"] == "slide" and event["place"]["index"] in (1, 2):
            who, text = IN_THE_DECK if event["place"]["index"] == 1 else BEYOND_THE_DECK
            if text not in asked:
                asked.add(text)
                await ws.send_json({"cmd": "ask", "who": who, "text": text})

    try:
        record = await listener.attend(str(server.make_url("/ws")), on_event=ask, timeout_s=3000)
    finally:
        await server.close()
        if hasattr(host, "close"):
            await host.close()
    events = record["events"]
    run = log.run_id()
    log.save_transcript(run, f"{args.host}-prepared-deck", record)

    TEST_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_DIR / "deck.md").write_text(_deck_markdown(store, deck.id))
    (TEST_DIR / "transcript.md").write_text(_transcript_markdown(store, lecture_id, events))

    turns = listener.slide_turns(events)
    result = {
        "deck": deck.subject, "slides": len(turns),
        "covered": [v["covered"] for v in store.path(lecture_id)],
        "first_audio_s": [t["first_audio_s"] for t in turns],
        "spoken_s": round(sum(e["seconds"] for e in events if e["type"] == "sentence")),
        "planned_s": sum(s.seconds or 0 for s in store.main_line(deck.id)),
        "diagrams_drawn_live": sum(t["diagrams"] for t in turns),
        "warnings": sum(t["warnings"] for t in turns),
        "questions": [{"text": q.text, "state": q.state} for q in store.questions(lecture_id)],
        "all_settled": all(q.state in SETTLED for q in store.questions(lecture_id)),
        "unexplained": metrics.unexplained(events),
    }
    log.record({"kind": "prepared-lecture", "host": args.host, "model": args.model, "run": run, "metrics": result})
    print(json.dumps(result, indent=1))
    print(f"wrote {TEST_DIR / 'deck.md'} and {TEST_DIR / 'transcript.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--repo", default=".")
    p.add_argument("--db", required=True)
    p.add_argument("--model", default="claude-opus-5-5")
    g = sub.add_parser("give")
    g.add_argument("--db", required=True)
    g.add_argument("--deck", type=int, required=True)
    g.add_argument("--host", choices=["hermes", "fake"], default="hermes")
    g.add_argument("--hermes-url", default=os.environ.get("HERMES_URL", "http://127.0.0.1:8642"))
    g.add_argument("--model", default=None, help="the model behind the host, for the log")
    args = ap.parse_args()
    asyncio.run(prepare(args) if args.command == "prepare" else give(args))


if __name__ == "__main__":
    main()
