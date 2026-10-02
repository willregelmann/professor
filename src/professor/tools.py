"""The agent's tools, identical in every host (invariants/interfaces/AGENT.md).

Hosts expose these however they expose tools (an MCP server for Claude Code, the
Agent SDK and Hermes) and pass calls through here. Every result is JSON. A tool
that fails says why, in terms the agent can act on: a slide that can't be shown
comes back as something to fix, never as a success.

So far: the read tools and the prepare tools. A lecturer acts on a lecture in
progress by marks in its speech; the tools of the same names come with the hosts
that can call a tool mid-turn.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from professor import showcheck
from professor.store import Slide, Store

_DECK = {"type": "integer", "description": "The deck's number."}
_SLIDE = {"type": "integer", "description": "The slide's id, as the deck tool lists it."}

SCHEMAS = [
    {"name": "deck",
     "description": "Read a deck: what it's about, who it's for, its main line (the slides in the order "
                    "they're given) and the slides beside it. With no deck, lists every deck. Never "
                    "returns speaker notes; read a slide for those.",
     "parameters": {"type": "object", "properties": {"deck": _DECK}}},
    {"name": "slide",
     "description": "Read one slide in full: what it shows, its speaker notes, its sources and whether "
                    "it can be shown.",
     "parameters": {"type": "object", "properties": {"slide": _SLIDE}, "required": ["slide"]}},
    {"name": "lecture",
     "description": "Read a lecture: one giving of a deck. Where it is, the path it took, which slides "
                    "were covered and which were skipped. With no lecture, reads the most recent one.",
     "parameters": {"type": "object", "properties": {
         "lecture": {"type": "integer", "description": "The lecture's number."},
         "deck": {"type": "integer", "description": "Read the most recent lecture of this deck."}}}},
    {"name": "questions",
     "description": "Read what the listeners asked in a lecture: who asked, what, on which slide, what "
                    "became of it and what was said in reply. With no lecture, reads the most recent one. "
                    "Use it when revising a deck, to see where it needs work.",
     "parameters": {"type": "object", "properties": {
         "lecture": {"type": "integer", "description": "The lecture's number."}}}},
    {"name": "describe",
     "description": "Say what a deck is about, who it's for and how long it should run. With no deck, "
                    "starts a new one. Anything left out stays as it was.",
     "parameters": {"type": "object", "properties": {
         "deck": {"type": "integer", "description": "The deck to change. Leave out to start a new deck."},
         "subject": {"type": "string", "description": "What the lecture is about, as a title."},
         "audience": {"type": "string", "description": "Who it's for, e.g. \"engineers new to this codebase\"."},
         "minutes": {"type": "integer", "description": "Roughly how long the whole lecture should run."},
         "background": {"type": "string",
                        "description": "What the lecturer should know and stay inside across every slide: "
                                       "facts, names and limits that no single slide's notes carry."}}}},
    {"name": "write_slide",
     "description": "Make a slide, or revise one. A slide is something to look at while one point is made, "
                    "plus speaker notes saying what to cover. Notes are not a script: the lecturer finds "
                    "the words. Put everything the lecturer needs in the notes, because it may not be able "
                    "to reach your sources. The diagram and equation are drawn in a check before the slide "
                    "is saved; if either can't be drawn, nothing is saved and the error comes back for you "
                    "to fix. A new slide goes at the end of the main line. When revising, anything left "
                    "out stays as it was.",
     "parameters": {"type": "object", "properties": {
         "deck": {"type": "integer", "description": "The deck a new slide belongs to."},
         "slide": {"type": "integer", "description": "The slide to revise. Leave out to make a new one."},
         "title": {"type": "string"},
         "notes": {"type": "string", "description": "Speaker notes: what to cover, never shown or read out."},
         "text": {"type": "string", "description": "A few lines shown on the slide. Plain text."},
         "diagram": {"type": "string", "description": "A Mermaid diagram, without the code fence."},
         "code": {"type": "string", "description": "A short piece of code shown on the slide."},
         "code_language": {"type": "string", "description": "The code's language, e.g. \"python\"."},
         "math": {"type": "string", "description": "One equation in LaTeX, shown in display style."},
         "sources": {"type": "array", "items": {"type": "string"},
                     "description": "Where the slide's claims come from, e.g. \"src/server.py:42\"."},
         "seconds": {"type": "integer", "description": "Roughly how long to speak on this slide."}}}},
    {"name": "correct",
     "description": "Keep a correction against a slide, or settle one. To keep one, give the slide and what "
                    "is wrong and what is right. Corrections are also kept by lecturers when a listener "
                    "catches a mistake; the slide tool lists them. To settle one after you have dealt with "
                    "it, give the correction's number and how it was settled: \"applied\" once the slide "
                    "has been revised, or \"set_aside\" if the slide was right after all, with why. A "
                    "correction is never deleted.",
     "parameters": {"type": "object", "properties": {
         "slide": {"type": "integer", "description": "The slide a new correction is about."},
         "text": {"type": "string", "description": "What is wrong on the slide, and what is right."},
         "correction": {"type": "integer", "description": "The correction to settle."},
         "settle": {"type": "string", "enum": ["applied", "set_aside"]},
         "why": {"type": "string", "description": "Why it was set aside, or what was changed."}}}},
    {"name": "arrange",
     "description": "Set a deck's main line: the slides given, in the order given, if nobody interrupts. "
                    "Any slide left out is kept beside the main line, not deleted.",
     "parameters": {"type": "object", "properties": {
         "deck": _DECK,
         "main_line": {"type": "array", "items": {"type": "integer"},
                       "description": "Slide ids, in order."}},
         "required": ["deck", "main_line"]}},
]

_SHOWN = ("title", "notes", "text", "diagram", "code", "code_language", "math", "sources", "seconds")


class ToolError(Exception):
    """Something the agent can act on."""


def _show(slide: Slide) -> str:
    if slide.show_error:
        return f"can't be shown: {slide.show_error}"
    if slide.diagram or slide.math:
        return "checked, can be shown" if slide.show_checked else "not checked"
    return "nothing to check"


class Tools:
    def __init__(self, store: Store, *, check: Callable[..., showcheck.Check] = showcheck.check):
        self.store = store
        self._check = check

    def call(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool. Returns its result as JSON, and whether it failed."""
        method = getattr(self, f"_{name}", None)
        if method is None or name not in {s["name"] for s in SCHEMAS}:
            return json.dumps({"error": f"there is no tool called {name!r}"}), True
        try:
            return json.dumps(method(**args), ensure_ascii=False, indent=1), False
        except (ToolError, KeyError, ValueError) as exc:
            return json.dumps({"error": exc.args[0] if exc.args else str(exc)}), True
        except TypeError as exc:
            return json.dumps({"error": f"{name} was called with arguments it doesn't take: {exc}"}), True

    # Read

    def _slide_line(self, slide: Slide) -> dict:
        shows = [what for what in ("text", "diagram", "code", "math") if getattr(slide, what)]
        line = {"slide": slide.id, "title": slide.title, "seconds": slide.seconds,
                "shows": shows, "sources": len(slide.sources), "show": _show(slide)}
        if still_open := sum(1 for c in self.store.corrections(slide_id=slide.id) if c.state == "open"):
            line["open_corrections"] = still_open  # read the slide to see them
        if slide.origin == "made_up":
            # Made up during a lecture: it stays beside the main line until someone puts it there.
            line["made_up"] = {"beside_slide": slide.anchor_slide_id,
                               "for_question": slide.question_id and self.store.question(slide.question_id).text}
        return line

    def _deck(self, deck: int | None = None) -> dict:
        if deck is None:
            return {"decks": [{"deck": d.id, "subject": d.subject, "audience": d.audience,
                               "slides": len(self.store.main_line(d.id))} for d in self.store.decks()]}
        d = self.store.deck(deck)
        main = self.store.main_line(d.id)
        return {"deck": d.id, "subject": d.subject, "audience": d.audience, "minutes": d.minutes,
                "background": d.background,
                "main_line": [{"number": i + 1, **self._slide_line(s)} for i, s in enumerate(main)],
                "planned_seconds": sum(s.seconds or 0 for s in main),
                "beside": [self._slide_line(s) for s in self.store.beside(d.id)],
                "lectures": [row["id"] for row in self.store.lectures(d.id)]}

    def _slide(self, slide: int) -> dict:
        s = self.store.slide(slide)
        return {"slide": s.id, "deck": s.deck_id,
                "on_main_line": s.position is not None, "number": None if s.position is None else s.position + 1,
                "origin": s.origin, "title": s.title, "text": s.text, "diagram": s.diagram, "code": s.code,
                "code_language": s.code_language, "math": s.math, "notes": s.notes, "sources": s.sources,
                "seconds": s.seconds, "show": _show(s),
                "corrections": [self._correction_line(c) for c in self.store.corrections(slide_id=s.id)]}

    def _correction_line(self, c) -> dict:
        asked = self.store.question(c.question_id).text if c.question_id else None
        return {"correction": c.id, "text": c.text, "state": c.state, "why": c.why,
                "from_lecture": c.lecture_id, "after_question": asked}

    def _latest_lecture(self, lecture: int | None, deck: int | None = None) -> dict:
        if lecture is not None:
            return self.store.lecture(lecture)
        row = self.store.db.execute(
            "SELECT id FROM lectures" + (" WHERE deck_id = ?" if deck is not None else "")
            + " ORDER BY id DESC LIMIT 1", (deck,) if deck is not None else ()).fetchone()
        if row is None:
            raise ToolError("no lecture has been given yet" + (f" from deck {deck}" if deck is not None else ""))
        return self.store.lecture(row[0])

    def _lecture(self, lecture: int | None = None, deck: int | None = None) -> dict:
        row = self._latest_lecture(lecture, deck)
        main = self.store.main_line(row["deck_id"])
        path = self.store.path(row["id"])
        covered = {v["slide_id"] for v in path if v["covered"]}
        title = {s.id: s.title for s in main + self.store.beside(row["deck_id"])}
        return {"lecture": row["id"], "deck": row["deck_id"], "host": row["host"], "state": row["state"],
                "message": row["state_message"], "at_slide": row["slide_id"],
                "path": [{"slide": v["slide_id"], "title": title.get(v["slide_id"]), "how": v["how"],
                          "covered": bool(v["covered"])} for v in path],
                "not_covered": [{"slide": s.id, "title": s.title} for s in main if s.id not in covered],
                "questions": dict(Counter(q.state for q in self.store.questions(row["id"])))}

    def _questions(self, lecture: int | None = None) -> dict:
        row = self._latest_lecture(lecture)
        out = []
        for q in self.store.questions(row["id"]):
            asked_on, just_said = self.store.moment(q)
            reply = self.store.reply(q.id)
            out.append({"question": q.id, "who": q.asker or "someone", "text": q.text, "state": q.state,
                        "asked_on": asked_on and {"slide": asked_on.id, "title": asked_on.title},
                        "just_after": just_said, "reason": q.reason,
                        "reply": reply if len(reply) <= 600 else reply[:600] + "…"})
        return {"lecture": row["id"], "deck": row["deck_id"], "questions": out}

    # Prepare

    def _describe(self, deck: int | None = None, subject: str | None = None, audience: str | None = None,
                  minutes: int | None = None, background: str | None = None) -> dict:
        if deck is None:
            if not (subject or "").strip():
                raise ToolError("a new deck needs a subject")
            deck = self.store.create_deck(subject.strip(), audience=audience or "", minutes=minutes,
                                          background=background or "").id
        else:
            self.store.describe_deck(deck, subject=subject, audience=audience, minutes=minutes,
                                     background=background)
        return self._deck(deck)

    def _write_slide(self, deck: int | None = None, slide: int | None = None, **fields) -> dict:
        if unknown := set(fields) - set(_SHOWN):
            raise ToolError(f"a slide has no {sorted(unknown)[0]!r}")
        if slide is None:
            if deck is None:
                raise ToolError("a new slide needs the deck it belongs to")
            self.store.deck(deck)
            current: dict[str, Any] = {}
        else:
            was = self.store.slide(slide)
            current = {k: getattr(was, k) for k in _SHOWN}
        new = {**current, **{k: v for k, v in fields.items() if v is not None}}
        if not (new.get("title") or "").strip():
            raise ToolError("a slide needs a title")
        if not (new.get("notes") or "").strip():
            raise ToolError("a slide needs speaker notes: what the lecturer should cover")

        # What's prepared can be shown. A slide that fails here is not saved.
        result = self._check(diagram=new.get("diagram") or "", math=new.get("math") or "")
        if result.checked and result.error:
            raise ToolError(f"Not saved. {result.error}\nFix it and write the slide again.")

        saved = (self.store.add_slide(deck, show_checked=result.ok, **new) if slide is None
                 else self.store.revise_slide(slide, show_checked=result.ok, **new))
        out = self._slide(saved.id)
        if not result.checked:
            out["warning"] = (f"Saved, but it could not be checked that this slide can be shown: {result.error}. "
                              "Check the diagram and equation by eye, or prepare where a browser is installed.")
        return out

    def _correct(self, slide: int | None = None, text: str | None = None, correction: int | None = None,
                 settle: str | None = None, why: str = "") -> dict:
        if correction is not None:
            if settle not in ("applied", "set_aside"):
                raise ToolError("to settle a correction, say how: \"applied\" or \"set_aside\"")
            if settle == "set_aside" and not why.strip():
                raise ToolError("a correction that is set aside needs the reason why")
            done = self.store.settle_correction(correction, settle, why.strip())
            return self._slide(done.slide_id)
        if slide is None or not (text or "").strip():
            raise ToolError("a new correction needs the slide it's about and what is wrong and right")
        self.store.slide(slide)
        self.store.correct(slide, text.strip())
        return self._slide(slide)

    def _arrange(self, deck: int, main_line: list[int]) -> dict:
        self.store.deck(deck)
        self.store.arrange(deck, main_line)
        return self._deck(deck)
