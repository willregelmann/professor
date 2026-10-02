"""The lecture loop: one giving of a deck, paced the way the room hears it.

The lecture owns the loop and asks the host for one turn at a time. The host's
text is cut into sentences and drawings, each sentence is turned into audio ahead
of time, and everything is sent to the listeners at the moment it should be heard
or seen. The lecture, not any one page, decides what is playing now, so every
listener hears the same words and a late arrival sees the lecture as it is.

Whatever goes wrong is shown, and nothing that goes wrong ends the lecture:
a sentence that can't be spoken, a diagram that won't draw, a host that can't be
reached.

Whatever the room says back is settled, whether or not the lecturer remembers to
settle it. A question is heard the moment it arrives. It is taken at the next
break, or straight away once a listener stops the lecturer. It ends answered,
put off or declined, and one that was put off is brought back.

The lecture is always at exactly one slide. The lecturer can show another slide,
or one it makes up on the spot, as a detour: the lecture remembers where it left,
detours can sit inside detours, and when the turn ends the screen is back where
it was whether or not the lecturer said so. Moving through a deck never changes
its order.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from professor import prompts, showcheck, speech
from professor.hosts.base import Host
from professor.splitter import Draw, Mark, Say, Splitter
from professor.store import SETTLED, Question, Slide, Store, Utterance

Subscriber = Callable[[dict], Awaitable[None]]

# What the lecturer is doing right now. Listeners always see one of these.
READY, THINKING, SPEAKING, LISTENING, PAUSED, STUCK, ENDED = (
    "ready", "thinking", "speaking", "listening", "paused", "stuck", "ended")

LONG_PAUSE_S = 120  # a pause longer than this gets a sentence picking the thread back up
FLOOR = "End of the main line. The floor is open."
HELD = "Stopped. Ask a question, or press Carry on."


@dataclass
class _HostFailed:
    reason: str


@dataclass
class _Drawing:
    """Something drawn onto the slide that's showing, during a turn."""
    utterance_id: int
    visit_id: int
    code: str
    checked: bool = False
    error: str | None = None


@dataclass
class _Picture:
    """Something the lecturer wrote to be looked at, with the result of its show check."""
    fields: dict            # title, text, diagram, code, code_language, math
    check: showcheck.Check
    own_slide: bool         # written as a slide of its own, not a drawing on the current one
    source: str             # what the lecturer wrote, for handing back if it can't be drawn


@dataclass
class _Frame:
    """A slide the lecture is at. The first frame is its place on the main line;
    each one after it is a detour from the one before."""
    slide: Slide
    visit: int
    place: dict


@dataclass
class _Resume:
    """Where a lecture that stopped is picked up, and what its lecturer is told."""
    index: int                # where on the main line to carry on; past the end means the floor
    carry: list[Utterance]    # what the room had heard of that slide when the lecture stopped
    note: str


@dataclass
class _Turn:
    """How a turn ended."""
    failure: str | None = None        # why the host couldn't be reached, if it couldn't
    stopped: bool = False             # a listener stopped the lecturer
    marks: list[Mark] = field(default_factory=list)
    drawings: list[_Drawing] = field(default_factory=list)
    failed: tuple[str, str] | None = None  # a picture that couldn't be drawn, and why


class Lecture:
    def __init__(self, store: Store, lecture_id: int, host: Host, *,
                 speak: Callable[[str], Awaitable[bytes]] = speech.speak,
                 check: Callable[..., showcheck.Check] = showcheck.check,
                 pause_between_s: float = 0.9,
                 resume_gap_s: float | None = None,
                 text_timeout_s: float = 60.0,
                 retry_delays_s: tuple[float, ...] = (2, 5, 15),
                 draw_report_grace_s: float = 0.4):
        self.store, self.id, self.host = store, lecture_id, host
        self.deck = store.deck(store.lecture(lecture_id)["deck_id"])
        self._speak = speak
        self._check = check
        self.pause_between_s = pause_between_s
        self.text_timeout_s = text_timeout_s
        self.retry_delays_s = retry_delays_s
        self.draw_report_grace_s = draw_report_grace_s

        self._subs: set[Subscriber] = set()
        self._state, self._message = READY, ""
        self._paused = False
        self._running = asyncio.Event()   # set unless paused
        self._running.set()
        self._pausing = asyncio.Event()   # set while paused
        self._started = asyncio.Event()
        self._retry = asyncio.Event()
        self._stop = asyncio.Event()      # a listener has stopped the lecturer
        self._carry_on = asyncio.Event()  # a listener has let it go on
        self._wake = asyncio.Event()      # the room did something the loop should look at
        self._task: asyncio.Task | None = None
        self._instructed = False          # has this host session had the standing instructions?
        self._main: list[Slide] = store.main_line(self.deck.id)
        self._frames: list[_Frame] = []   # where the lecture is: its place, then any detours
        self._jump: int | None = None     # where on the main line the lecturer asked to carry on from
        self._slide: Slide | None = None  # what's on screen: the last frame's
        self._visit: int | None = None
        self._place: dict | None = None
        self._figure: dict | None = None  # a drawing made during the turn that's on screen now
        self._drawings: dict[str, _Drawing] = {}
        self._floor_open = False
        self._paused_at: float | None = None
        self._note: str | None = None     # something the lecturer is told at the start of its next turn
        # A lecture that has been somewhere already is being picked up, not started.
        self._resume = self._plan_resume(resume_gap_s) if store.path(lecture_id) else None

    # What listeners see

    def subscribe(self, sub: Subscriber) -> None:
        self._subs.add(sub)

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def snapshot(self) -> dict:
        """The lecture as it is now, for someone who has just arrived."""
        return {
            "type": "hello",
            "deck": {"subject": self.deck.subject, "audience": self.deck.audience},
            "slide": self._slide_message()["slide"] if self._slide else None,
            "place": self._place,
            "diagram": self._figure,
            "state": PAUSED if self._paused else self._state,
            "message": self._message,
            "held": self._message == HELD,
            "floor": self._floor_open,
            "questions": [self._question(q) for q in self.store.questions(self.id)],
        }

    def _slide_message(self) -> dict:
        return {"type": "slide", "slide": {**self._slide.shown(), "revision": self._slide.revision_id},
                "place": self._place}

    def _question(self, q: Question) -> dict:
        """A question as the chat shows it: what was asked and what became of it."""
        until = None
        if q.state == "put_off":
            index = next((i for i, s in enumerate(self._main) if s.id == q.until_slide_id), None)
            until = "the end" if index is None else f"slide {index + 1}"
        return {"id": q.id, "who": q.asker, "text": q.text, "state": q.state, "until": until,
                "reason": q.reason, "reply": self.store.reply(q.id),
                "corrections": [c.text for c in self.store.corrections(question_id=q.id)],
                "slides": [slide.title for slide in self.store.made_for(q.id)]}

    async def _send(self, msg: dict) -> None:
        for sub in list(self._subs):
            try:
                await sub(msg)
            except Exception:
                self._subs.discard(sub)  # a listener who has gone doesn't hold up the room

    async def _set_state(self, state: str, message: str = "") -> None:
        if (state, message) == (self._state, self._message):
            return
        self._state, self._message = state, message
        self.store.set_state(self.id, state, message)
        if not self._paused:
            await self._send({"type": "state", "state": state, "message": message, "held": message == HELD})

    async def _set_question(self, q: Question, state: str, **fields) -> Question:
        q = self.store.set_question(q.id, state, **fields)
        await self._send({"type": "question", "question": self._question(q)})
        return q

    # What listeners can do

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        self._started.set()

    async def pause(self) -> None:
        if self._paused or self._state in (READY, ENDED):
            return
        self._paused = True
        self._paused_at = time.monotonic()
        self._running.clear()
        self._pausing.set()
        await self._send({"type": "state", "state": PAUSED, "message": "", "held": False})

    async def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        paused_for = time.monotonic() - (self._paused_at or time.monotonic())
        if paused_for >= LONG_PAUSE_S:
            self._note = prompts.after_pause(paused_for)  # a recap sized to the gap, at its next turn
        self._pausing.clear()
        self._running.set()
        await self._send({"type": "state", "state": self._state, "message": self._message,
                          "held": self._message == HELD})

    def retry(self) -> None:
        """Try the host again now, without waiting out the delay."""
        self._retry.set()

    async def ask(self, who: str, text: str) -> None:
        """A listener has said something back. It's kept and shown before any agent sees it."""
        who, text = who.strip()[:60], text.strip()[:1000]
        if not text:
            return
        if self._state == ENDED:
            await self._send({"type": "warn", "message": "The lecture has ended, so that wasn't taken."})
            return
        # A lecturer that can't be reached hasn't heard anything. The question waits.
        q = self.store.ask(self.id, who, text, state="waiting" if self._state == STUCK else "heard")
        await self._send({"type": "question", "question": self._question(q)})
        self._wake.set()

    async def stop(self) -> None:
        """A listener stops the lecturer. It listens until a question comes or it's told to carry on."""
        if self._state in (READY, ENDED, STUCK) or self._message in (HELD, FLOOR):
            return  # there is nothing being said to stop
        await self.resume()
        self._stop.set()

    def carry_on(self) -> None:
        self._carry_on.set()
        self._wake.set()

    async def close(self) -> None:
        """Stop the loop without ending the lecture: the server is going away."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        with contextlib.suppress(Exception):
            await self.host.cancel()

    async def end(self) -> None:
        if self._state == ENDED:
            return
        await self.close()
        self._paused = False
        self._pausing.clear()
        self._running.set()
        # No question is left hanging: whatever wasn't taken is declined, and says why.
        for q in self.store.questions(self.id):
            if q.state not in SETTLED:
                await self._set_question(q, "declined", reason="The lecture ended before this was taken.")
        await self._set_state(ENDED, "The lecture has ended.")

    async def draw_failed(self, drawing_id: str, error: str) -> None:
        """A page couldn't draw something the lecturer drew during its turn."""
        drawing = self._drawings.get(drawing_id)
        if drawing is None or drawing.error is not None:
            return  # unknown, or another page already said so
        drawing.error = error[:400]
        self.store.record_draw_failure(drawing.utterance_id, drawing.error)
        await self._send({"type": "warn", "message": "A diagram couldn't be drawn."})

    async def slide_failed(self, revision_id: int, error: str) -> None:
        """A page couldn't draw the current slide's own diagram."""
        if self._slide is None or self._slide.revision_id != revision_id or self._slide.show_error:
            return
        self.store.record_show_error(revision_id, error[:400])
        self._slide = self._frames[-1].slide = self.store.slide(self._slide.id)
        await self._send({"type": "warn", "message": "This slide's diagram couldn't be drawn."})

    # The loop

    async def _run(self) -> None:
        await self._started.wait()
        try:
            n, i, last, carry = len(self._main), 0, None, []
            if self._resume is not None:
                i, carry, self._note = self._resume.index, self._resume.carry, self._resume.note
                if not carry and 0 < i < n and self.store.questions(self.id, "heard"):
                    # It stopped between slides with something still unanswered. That is
                    # taken where it was asked, before the lecture moves on.
                    await self._stand_at(i - 1)
                    await self._attend()
                    last = i - 1
            while True:
                while i < n:
                    self._jump = None
                    how = ("resume" if self._resume is not None and not self._frames else
                           "start" if last is None else "next" if i == last + 1 else "jump")
                    await self._give(i, how, carry)
                    carry = []
                    if self._jump is None:  # otherwise the slide was left for somewhere else
                        await self._come_back(self._main[i])
                        await self._attend()
                        await self._wait(self.pause_between_s)
                        await self._attend()
                    last, i = i, self._landing(i + 1)
                # Reaching the end of the main line doesn't end the lecture. It opens the floor.
                self._floor_open, self._jump = True, None
                if not self._frames:
                    # Picked up after the last slide had been given: the lecturer says it's back.
                    await self._stand_at(n - 1)
                    await self._take_turn(lambda: prompts.BACK_AT_THE_END)
                await self._send({"type": "floor"})
                await self._come_back(None)
                while True:
                    await self._attend()
                    if self._jump is not None:
                        if self._landing(n) < n:
                            break
                        self._jump = None  # "next" from the end goes nowhere
                    self._wake.clear()
                    if self.store.questions(self.id, "heard") or self._stop.is_set():
                        continue
                    await self._set_state(LISTENING, FLOOR)
                    await self._wake.wait()
                # The lecturer went back into the deck. The lecture carries on from there.
                i, self._floor_open = self._landing(n), False
        except Exception as exc:
            # A fault in the lecture itself. It can't go on, and the room is told why.
            await self._set_state(STUCK, f"The lecture has stopped: {exc or type(exc).__name__}")
            raise

    async def _stand_at(self, index: int) -> None:
        """Put a slide that has already been given back on the screen, without giving
        it again, when a lecture is picked up after it."""
        slide = self.store.slide(self._main[index].id)
        place = {"index": index, "of": len(self._main), "detour": False}
        self._frames = [_Frame(slide, self.store.arrive(self.id, slide, "resume"), place)]
        self._slide, self._visit, self._place, self._figure = slide, self._frames[0].visit, place, None
        await self._send(self._slide_message())

    def _landing(self, otherwise: int) -> int:
        """Where on the main line the lecture goes next: where the lecturer asked
        to carry on from, if it did, or else straight on."""
        return otherwise if self._jump is None else max(0, min(self._jump, len(self._main)))

    async def _give(self, index: int, how: str, carry: list[Utterance] = []) -> None:
        """Give a slide. `carry` is what the room heard of it before the lecture
        stopped, when a lecture is picked up partway through one."""
        slide = self.store.slide(self._main[index].id)
        visit = self.store.arrive(self.id, slide, how)
        place = {"index": index, "of": len(self._main), "detour": False}
        self._frames = [_Frame(slide, visit, place)]
        self._slide, self._visit, self._place, self._figure = slide, visit, place, None
        await self._send(self._slide_message())

        # Coming back to a slide that has already been given isn't giving it again.
        before = "" if carry else self.store.said_on(self.id, slide.id, visit)
        # A listener can stop the lecturer partway. Once the room has been
        # attended to, the slide is picked up from where it was cut off, unless
        # the room asked to move on.
        while (await self._take_turn(lambda: prompts.present(
                self.deck, self.store.slide(slide.id), index, len(self._main),
                carry + self.store.utterances(self.id, visit), before=before))).stopped:
            await self._attend()
            if self._jump is not None:
                return  # left unfinished, so not covered
        self.store.mark_covered(visit)

    async def _take_turn(self, prompt: Callable[[], str], question: Question | None = None) -> _Turn:
        """Give the lecturer a turn, and settle what it leaves undone. If something
        it drew couldn't be shown, it gets one more turn to draw it again or carry
        on without it. If it left a detour open, it gets one to say it's going back."""
        turn = await self._turn_until_heard(prompt, question)
        if not turn.stopped:
            await self._redraw(turn, question)
        if not turn.stopped and len(self._frames) > 1:
            # The lecturer left a detour open. It is asked to say it's going back, so
            # the room hears where the screen is going before it goes.
            base = self._frames[0]
            closing = await self._turn_until_heard(
                lambda: prompts.close_detour(base.place["index"], base.slide.title), question)
            turn.marks += closing.marks
            turn.stopped = closing.stopped
        # Whatever was said, the screen ends the turn back where the lecture left.
        await self._return(0)
        return turn

    async def _redraw(self, turn: _Turn, question: Question | None) -> None:
        failed = turn.failed
        if failed is None and self._subs and any(not d.checked for d in turn.drawings):
            # Nothing could check these drawings here, so a page is the first to try.
            await self._wait(self.draw_report_grace_s)  # the last one's report may still be on its way
            failed = next(((d.code, d.error) for d in reversed(turn.drawings) if d.error), None)
        if failed:
            again = await self._turn_until_heard(lambda: prompts.redraw(*failed), question)
            turn.marks += again.marks
            turn.stopped = again.stopped

    async def _correct(self, text: str, question: Question | None) -> None:
        """The lecturer found something wrong. The correction is kept against the
        slide it concerns: the one the question was asked on, or the one being given."""
        if not text.strip():
            return
        slide = (self.store.moment(question)[0] if question else None) or self._frames[0].slide
        self.store.correct(slide.id, text.strip(), lecture_id=self.id, question_id=question.id if question else None)
        if question:
            await self._send({"type": "question", "question": self._question(self.store.question(question.id))})

    # Picking a lecture back up

    def _plan_resume(self, gap_s: float | None) -> _Resume:
        """Work out where a stopped lecture is picked up, from what it kept."""
        row = self.store.lecture(self.id)
        if row["state"] == ENDED:
            raise ValueError(f"lecture {self.id} has ended. Giving the deck again is a new lecture.")
        path = self.store.path(self.id)
        now = [s.id for s in self._main]
        then = json.loads(row["main_line"]) if row["main_line"] else now
        on_line = [v for v in path if v["how"] in ("start", "next", "jump", "resume")]
        last = on_line[-1]

        # Where to carry on: the slide it stopped in, the one after a slide it had
        # finished, or the nearest slide still there if that one has gone.
        changes, carry = [], []
        if last["slide_id"] in now:
            index = now.index(last["slide_id"]) + (1 if last["covered"] else 0)
            if not last["covered"]:
                # Everything the room has heard of this slide, however many times the lecture stopped in it.
                unfinished = {v["visit_id"] for v in on_line
                              if v["slide_id"] == last["slide_id"] and not v["covered"]}
                carry = [u for u in self.store.utterances(self.id)
                         if u.visit_id in unfinished and u.question_id is None]
        else:
            later = [sid for sid in then[then.index(last["slide_id"]) + 1:] if sid in now] \
                if last["slide_id"] in then else []
            index = now.index(later[0]) if later else len(now)
            gone = self.store.slide(last["slide_id"]).title
            where = f"slide {index + 1}, \"{self._main[index].title}\"" if index < len(now) else "the end"
            changes.append(f"The slide the lecture stopped on, \"{gone}\", has been taken out of the deck, "
                           f"so the lecture resumes at the nearest point that is left: {where}.")

        # What else changed in the deck, that the room should be told.
        given = [v for v in on_line if v["covered"]]
        for v in given:
            if v["slide_id"] in now and self.store.slide(v["slide_id"]).revision_id != v["slide_revision_id"]:
                changes.append(f"The slide \"{self.store.slide(v['slide_id']).title}\", which was already "
                               "given, has been revised since.")
        for sid in then:
            if sid not in now and sid != last["slide_id"]:
                changes.append(f"The slide \"{self.store.slide(sid).title}\" has been taken out of the deck.")
        for sid in now:
            if sid not in then:
                changes.append(f"A slide has been added: \"{self.store.slide(sid).title}\".")

        # Questions that were mid-air when it stopped are heard again, not lost.
        for q in self.store.questions(self.id):
            if q.state in ("answering", "waiting"):
                self.store.set_question(q.id, "heard")

        if gap_s is None:
            stopped = self.store.last_activity(self.id)
            gap_s = (dt.datetime.now(dt.timezone.utc) - stopped).total_seconds() if stopped else 0.0
        said = {v["visit_id"]: " ".join(u.text for u in self.store.utterances(self.id, v["visit_id"])
                                        if u.kind == "said" and not u.failed and u.question_id is None)
                for v in on_line}
        note = prompts.resume(
            gap_s,
            given=[(self.store.slide(v["slide_id"]).title, said[v["visit_id"]]) for v in on_line
                   if v["covered"] or said[v["visit_id"]]],
            questions=[(q, self.store.reply(q.id)) for q in self.store.questions(self.id)],
            changes=changes,
            stopped_in=next((u.text for u in reversed(carry) if u.kind == "said" and not u.failed), None))

        # Someone who opens the page before it's picked up sees where it stopped.
        if index < len(now):
            self._slide, self._place = self._main[index], {"index": index, "of": len(now), "detour": False}
        elif now:
            self._slide, self._place = self._main[-1], {"index": len(now) - 1, "of": len(now), "detour": False}
        self._message = f"This lecture stopped {prompts.ago(gap_s)} ago. Press Start to pick it up."
        self.store.set_state(self.id, READY, self._message)
        return _Resume(index, carry, note)

    # Moving

    async def _show(self, slide: Slide, how: str, place: dict) -> None:
        """Leave the slide that's showing for another, remembering the way back."""
        visit = self.store.arrive(self.id, slide, how)
        self._frames.append(_Frame(slide, visit, place))
        self._slide, self._visit, self._place, self._figure = slide, visit, place, None
        await self._send(self._slide_message())

    async def _return(self, to_depth: int) -> None:
        """Come back from detours until only `to_depth` are left open."""
        if len(self._frames) - 1 <= to_depth:
            return
        del self._frames[to_depth + 1:]
        frame = self._frames[-1]
        self.store.arrive(self.id, frame.slide, "return")  # the path keeps the way back too
        # What's said from here belongs to the visit that was interrupted.
        self._slide, self._visit, self._place, self._figure = frame.slide, frame.visit, frame.place, None
        await self._send(self._slide_message())

    async def _go(self, body: str) -> None:
        """The lecturer moves the lecture: a detour to a slide, back from one, or
        on along the main line from somewhere else."""
        word = body.strip().lower()
        number = re.search(r"\d+", word)
        base = self._frames[0].place["index"]
        target = None
        if word.startswith("return"):
            return await self._return(max(0, len(self._frames) - 2))
        if word.startswith("next"):
            self._jump = base + 1
            return
        if word.startswith("from") and number and 1 <= int(number.group()) <= len(self._main):
            self._jump = int(number.group()) - 1
            return
        if word.startswith("back") and not number:
            target = base - 1
        elif number and not word.startswith("from"):
            target = int(number.group()) - 1
        if target is None or not 0 <= target < len(self._main):
            await self._send({"type": "warn", "message": "The lecturer tried to move to a slide that isn't there."})
            return
        slide = self.store.slide(self._main[target].id)
        if slide.id != self._slide.id:
            await self._show(slide, "detour", {"index": target, "of": len(self._main),
                                               "detour": len(self._frames), "from": base})

    async def _make_up(self, picture: _Picture, question: Question | None) -> None:
        """A slide made up on the spot. It's shown as a detour and kept beside the
        main line, with the question it was made for. The main line doesn't change."""
        base = self._frames[0]
        made_for = (f" in answer to {question.asker or 'someone'}, who asked: {question.text}"
                    if question else "")
        fields = dict(picture.fields)
        title = fields.pop("title", "") or (question.text[:80] if question else "A sketch")
        slide = self.store.add_slide(
            self.deck.id, title=title, notes=f"Made up during a lecture{made_for}.",
            origin="made_up", on_main_line=False, anchor_slide_id=base.slide.id,
            question_id=question.id if question else None, show_checked=picture.check.ok, **fields)
        await self._show(slide, "made up", {"index": None, "of": len(self._main), "detour": len(self._frames),
                                            "from": base.place["index"], "made_up": True})

    # What the room says back

    async def _attend(self) -> None:
        """Attend to the room: wait if a listener has stopped the lecturer, then
        take every question that's waiting, in the order they were asked."""
        while True:
            if self._stop.is_set():
                await self._hold()
            heard = self.store.questions(self.id, "heard")
            if not heard:
                return
            await self._answer(heard[0], prompts.answer(
                heard[0], *self.store.moment(heard[0]), on_slide=self._frames[0].slide, slides=self._main,
                can_put_off=not self._floor_open, stopped_after=self._stopped_after()),
                can_put_off=not self._floor_open)

    def _stopped_after(self) -> str | None:
        """If a listener stopped the lecturer partway through the slide it's on,
        the last thing the room heard of that slide. The lecturer's own record of
        its turn says it said everything, so it has to be told what was heard."""
        said = [u for u in self.store.utterances(self.id, self._frames[0].visit)
                if u.kind == "said" and u.question_id is None]
        if not said or said[-1].failed not in ("cut off", "unsaid"):
            return None
        heard = [u.text for u in said if not u.failed]
        return heard[-1] if heard else ""

    async def _hold(self) -> None:
        """The lecturer has been stopped. It listens until a question comes or it's let go on."""
        self._stop.clear()
        self._carry_on.clear()
        while not self._carry_on.is_set() and not self.store.questions(self.id, "heard"):
            await self._set_state(LISTENING, HELD)
            self._wake.clear()
            await self._wake.wait()
        await self._set_state(LISTENING)

    async def _come_back(self, slide: Slide | None) -> None:
        """Bring back the questions that were put off to this slide, or, at the
        end of the main line, every question still put off."""
        for q in self.store.questions(self.id, "put_off"):
            if slide is None or q.until_slide_id == slide.id:
                await self._answer(q, prompts.come_back(q, self.store.reply(q.id), at_end=slide is None),
                                   can_put_off=False)

    async def _answer(self, q: Question, prompt: str, *, can_put_off: bool) -> None:
        """Take a question. Whatever the lecturer does, it ends answered, put off or declined."""
        if self._stop.is_set():
            await self._hold()
        q = await self._set_question(q, "answering")
        turn = await self._take_turn(lambda: prompt, question=q)
        marks = {m.kind: m.body for m in turn.marks}
        if "decline" in marks:
            await self._set_question(q, "declined", reason=marks["decline"] or "The lecturer gave no reason.")
        elif "later" in marks and can_put_off and not turn.stopped:
            await self._set_question(q, "put_off", until_slide_id=self._until(marks["later"]))
        else:
            await self._set_question(q, "answered")

    def _until(self, body: str) -> int | None:
        """Where a question was put off to: a slide still to come, or (None) the end."""
        digits = "".join(c for c in body if c.isdigit())
        index = int(digits) - 1 if digits else -1
        here = self._frames[0].place["index"] if self._frames else -1
        return self._main[index].id if here < index < len(self._main) else None

    # Turns

    async def _turn_until_heard(self, prompt: Callable[[], str], question: Question | None = None) -> _Turn:
        """Run a turn, and keep trying while the host can't be reached."""
        if self._stop.is_set():
            await self._hold()  # stopped between turns: nothing more is said until the room lets it
        attempt = 0
        while (turn := await self._turn(prompt(), question)).failure is not None:
            delay = self.retry_delays_s[attempt] if attempt < len(self.retry_delays_s) else None
            again = (f"Trying again in {delay:g} seconds." if delay is not None
                     else "Press Try again when it's back.")
            await self._set_state(STUCK, f"The lecturer can't be reached ({turn.failure}). {again}")
            self._retry.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._retry.wait(), timeout=delay)
            attempt += 1
        # The lecturer is back, so it has now heard whatever was asked while it was away.
        for waiting in self.store.questions(self.id, "waiting"):
            await self._set_question(waiting, "heard")
        return turn

    async def _turn(self, prompt: str, question: Question | None = None) -> _Turn:
        """One turn of the host, heard through to the end, or until a listener stops it."""
        qid = question.id if question else None
        if self._note:
            prompt, self._note = self._note + "\n\n" + prompt, None
        if not self._instructed:
            prompt = prompts.standing(self.deck, self._main) + "\n\n" + prompt
        items: asyncio.Queue = asyncio.Queue()   # sentences, drawings and marks, as written
        ready: asyncio.Queue = asyncio.Queue()   # the same, with audio, in the same order
        splitter = Splitter()
        got_text = False

        async def read() -> None:
            nonlocal got_text
            failure = None
            stream = self.host.turn(prompt).__aiter__()
            try:
                while True:
                    try:
                        delta = await asyncio.wait_for(stream.__anext__(), self.text_timeout_s)
                    except StopAsyncIteration:
                        break
                    got_text = True
                    for item in splitter.feed(delta):
                        await items.put(item)
            except TimeoutError:
                failure = f"no word from it in {self.text_timeout_s:g} seconds"
                with contextlib.suppress(Exception):
                    await self.host.cancel()
            except Exception as exc:
                failure = str(exc) or type(exc).__name__
            if failure is None:
                for item in splitter.close():
                    await items.put(item)
            # On failure the unfinished tail is dropped: half a sentence isn't said.
            await items.put(_HostFailed(failure) if failure else None)

        async def synthesize() -> None:
            while True:
                item = await items.get()
                if isinstance(item, Say):
                    item = (item.text, await self._audio(item.text))
                elif isinstance(item, Draw) or (isinstance(item, Mark) and item.kind == "slide"):
                    item = await self._picture(item)
                await ready.put(item)
                if item is None or isinstance(item, _HostFailed):
                    return

        tasks = [asyncio.create_task(read()), asyncio.create_task(synthesize())]
        turn = _Turn()
        playing: int | None = None  # the utterance being heard right now
        unheard: list[str] = []     # what a stop kept the room from hearing, in order
        try:
            while True:
                if ready.empty():
                    await self._set_state(THINKING)
                item = await self._unless_stopped(ready.get())
                if item is not _STOPPED:
                    await self._unless_stopped(self._running.wait())
                if self._stop.is_set():
                    turn.stopped = True
                    if isinstance(item, tuple):
                        unheard.append(item[0])  # ready, but the stop came first
                    break
                if item is None:
                    break
                if isinstance(item, _HostFailed):
                    turn.failure = item.reason
                    break
                if isinstance(item, Mark):
                    if item.kind == "go":
                        await self._go(item.body)
                    elif item.kind == "correct":
                        await self._correct(item.body, question)
                    else:
                        turn.marks.append(item)
                    continue
                if isinstance(item, _Picture):
                    await self._draw(item, turn, question)
                    continue
                text, audio = item
                if not audio:
                    # Shown, not heard. The turn carries on.
                    reason = "the speech engine returned no audio"
                    uid = self.store.utter(self.id, self._visit, "said", text, failed=reason, question_id=qid)
                    await self._send({"type": "unspoken", "id": uid, "text": text, "reason": reason})
                    continue
                playing = self.store.utter(self.id, self._visit, "said", text, question_id=qid)
                seconds = speech.duration_s(audio)
                await self._set_state(SPEAKING)
                await self._send({"type": "sentence", "id": playing, "text": text, "seconds": round(seconds, 3),
                                  "audio": base64.b64encode(audio).decode()})
                if not await self._play(seconds, stoppable=True):
                    turn.stopped = True
                    break
                playing = None
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if got_text:
            self._instructed = True
            self.store.set_host_session(self.id, self.host.session)
        if turn.stopped:
            await self._cut_off(playing, unheard, items, ready, splitter, self._visit, qid)
        if turn.stopped:
            await self._return(0)  # a stopped lecturer isn't left standing on a detour
        if not turn.stopped and turn.failure is None:
            await self._set_state(LISTENING)
        return turn

    async def _picture(self, item: Draw | Mark) -> _Picture:
        """Read what the lecturer wrote to be looked at, and check it can be drawn."""
        if isinstance(item, Draw):
            fields, source, own_slide = {"diagram": item.code}, item.code, False
        else:
            fields, source, own_slide = _slide_fields(item.body), item.body, True
        check = await asyncio.to_thread(self._check, diagram=fields.get("diagram", ""), math=fields.get("math", ""))
        return _Picture(fields, check, own_slide, source)

    async def _draw(self, picture: _Picture, turn: _Turn, question: Question | None) -> None:
        """Put something the lecturer wrote on the screen, at the point its speech reached it."""
        qid = question.id if question else None
        shown = picture.fields.get("diagram") or picture.source
        if picture.check.checked and picture.check.error:
            # Held to the same rule as a prepared slide: what can't be drawn isn't shown.
            self.store.utter(self.id, self._visit, "drawn", shown, failed=picture.check.error, question_id=qid)
            turn.failed = (picture.source, picture.check.error)
            await self._send({"type": "warn", "message": "A diagram couldn't be drawn."})
        elif picture.own_slide or question is not None:
            # An answer that needs a picture makes a slide, kept with the deck.
            if any(picture.fields.get(k) for k in ("text", "diagram", "code", "math")):
                await self._make_up(picture, question)
        else:
            uid = self.store.utter(self.id, self._visit, "drawn", shown, question_id=qid)
            drawing = _Drawing(uid, self._visit, shown, checked=picture.check.checked)
            self._drawings[f"d{uid}"] = drawing
            turn.drawings.append(drawing)
            self._figure = {"id": f"d{uid}", "code": shown}
            await self._send({"type": "diagram", **self._figure})

    async def _cut_off(self, playing: int | None, unsaid: list[str], items: asyncio.Queue,
                       ready: asyncio.Queue, splitter: Splitter, visit: int, qid: int | None) -> None:
        """A listener stopped the lecturer. The room goes quiet at once, and the
        thought that was cut off is kept so the lecturer can pick it up again."""
        await self._send({"type": "stopped"})
        with contextlib.suppress(Exception):
            await self.host.cancel()
        if playing is not None:
            self.store.cut_off(playing)
        for queue in (ready, items):  # what was ready comes before what was only written
            while not queue.empty():
                item = queue.get_nowait()
                if isinstance(item, Say):
                    unsaid.append(item.text)
                elif isinstance(item, tuple):
                    unsaid.append(item[0])
        unsaid.append(splitter.unsaid())
        if text := " ".join(t for t in unsaid if t):
            self.store.utter(self.id, visit, "said", text, failed="unsaid", question_id=qid)

    async def _unless_stopped(self, awaitable: Awaitable):
        """Wait for something, or for a listener to stop the lecturer, whichever is first."""
        wanted = asyncio.ensure_future(awaitable)
        stop = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({wanted, stop}, return_when=asyncio.FIRST_COMPLETED)
            return wanted.result() if wanted.done() else _STOPPED
        finally:
            for future in (wanted, stop):
                future.cancel()

    async def _audio(self, text: str) -> bytes:
        """Audio for a sentence, trying once more if the engine comes back empty."""
        for _ in range(2):
            try:
                if audio := await self._speak(text):
                    return audio
            except Exception:
                pass
        return b""

    async def _play(self, seconds: float, stoppable: bool = False) -> bool:
        """Wait while a clip plays. The clock stops while the lecture is paused.
        Returns False if a listener stopped the lecturer first."""
        while seconds > 0:
            await self._running.wait()
            began = time.monotonic()
            waiting = [asyncio.ensure_future(self._pausing.wait()), asyncio.ensure_future(self._stop.wait())]
            try:
                done, _ = await asyncio.wait(waiting, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for future in waiting:
                    future.cancel()
            if self._stop.is_set():
                return not stoppable
            if not done:
                return True
            seconds -= time.monotonic() - began
        return True

    _wait = _play  # a silence the lecture chooses is paused the same way, and ends early on a stop


_STOPPED = object()


_SECTION = re.compile(r"^(title|text|diagram|code|language|math)\s*:\s*(.*)$", re.I)


def _slide_fields(body: str) -> dict:
    """What a ```slide block holds: each part starts with a line naming it, and
    runs until the next such line.

        title: How a question moves
        diagram:
        flowchart LR
          A --> B
    """
    fields: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        found = _SECTION.match(line)
        if found:
            current = found.group(1).lower()
            fields[current] = [found.group(2)] if found.group(2).strip() else []
        elif current is not None:
            fields[current].append(line)
    out = {name: "\n".join(lines).strip() for name, lines in fields.items()}
    if "language" in out:
        out["code_language"] = out.pop("language")
    return {name: value for name, value in out.items() if value}
