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
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from professor import prompts, speech
from professor.hosts.base import Host
from professor.splitter import Draw, Mark, Say, Splitter
from professor.store import SETTLED, Question, Slide, Store

Subscriber = Callable[[dict], Awaitable[None]]

# What the lecturer is doing right now. Listeners always see one of these.
READY, THINKING, SPEAKING, LISTENING, PAUSED, STUCK, ENDED = (
    "ready", "thinking", "speaking", "listening", "paused", "stuck", "ended")

FLOOR = "End of the main line. The floor is open."
HELD = "Stopped. Ask a question, or press Carry on."


@dataclass
class _HostFailed:
    reason: str


@dataclass
class _Drawing:
    utterance_id: int
    visit_id: int
    code: str
    error: str | None = None


@dataclass
class _Turn:
    """How a turn ended."""
    failure: str | None = None        # why the host couldn't be reached, if it couldn't
    stopped: bool = False             # a listener stopped the lecturer
    marks: list[Mark] = field(default_factory=list)


class Lecture:
    def __init__(self, store: Store, lecture_id: int, host: Host, *,
                 speak: Callable[[str], Awaitable[bytes]] = speech.speak,
                 pause_between_s: float = 0.9,
                 text_timeout_s: float = 60.0,
                 retry_delays_s: tuple[float, ...] = (2, 5, 15),
                 draw_report_grace_s: float = 0.4):
        self.store, self.id, self.host = store, lecture_id, host
        self.deck = store.deck(store.lecture(lecture_id)["deck_id"])
        self._speak = speak
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
        self._slide: Slide | None = None
        self._visit: int | None = None
        self._place: dict | None = None
        self._figure: dict | None = None  # a drawing made during the turn that's on screen now
        self._drawings: dict[str, _Drawing] = {}
        self._floor_open = False

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
                "reason": q.reason, "reply": self.store.reply(q.id)}

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
        self._running.clear()
        self._pausing.set()
        await self._send({"type": "state", "state": PAUSED, "message": "", "held": False})

    async def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
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
        self._slide = self.store.slide(self._slide.id)
        await self._send({"type": "warn", "message": "This slide's diagram couldn't be drawn."})

    # The loop

    async def _run(self) -> None:
        await self._started.wait()
        try:
            n = len(self._main)
            for i, slide in enumerate(self._main):
                await self._give(self.store.slide(slide.id), i, n, how="start" if i == 0 else "next")
                await self._come_back(slide)
                await self._attend()
                await self._wait(self.pause_between_s)
                await self._attend()
            # Reaching the end of the main line doesn't end the lecture. It opens the floor.
            self._floor_open = True
            await self._send({"type": "floor"})
            await self._come_back(None)
            while True:
                await self._attend()
                self._wake.clear()
                if self.store.questions(self.id, "heard") or self._stop.is_set():
                    continue
                await self._set_state(LISTENING, FLOOR)
                await self._wake.wait()
        except Exception as exc:
            # A fault in the lecture itself. It can't go on, and the room is told why.
            await self._set_state(STUCK, f"The lecture has stopped: {exc or type(exc).__name__}")
            raise

    async def _give(self, slide: Slide, index: int, n: int, how: str) -> None:
        visit = self.store.arrive(self.id, slide, how)
        self._slide, self._visit, self._figure = slide, visit, None
        self._place = {"index": index, "of": n, "detour": False}
        await self._send(self._slide_message())

        # A listener can stop the lecturer partway. Once the room has been
        # attended to, the slide is picked up from where it was cut off.
        while (await self._turn_until_heard(lambda: prompts.present(
                self.deck, self._slide, index, n, self.store.utterances(self.id, visit)))).stopped:
            await self._attend()

        # A drawing made during the turn may have failed on a page. The lecturer
        # gets one more turn to draw it again, or to carry on without it.
        mine = [d for d in self._drawings.values() if d.visit_id == visit]
        if mine and self._subs:
            await self._wait(self.draw_report_grace_s)  # the last drawing's report may still be on its way
        failed = [d for d in mine if d.error]
        if failed:
            await self._turn_until_heard(lambda: prompts.redraw(failed[-1].code, failed[-1].error))
        self.store.mark_covered(visit)

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
                heard[0], *self.store.moment(heard[0]), on_slide=self._slide, slides=self._main,
                can_put_off=not self._floor_open), can_put_off=not self._floor_open)

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
        turn = await self._turn_until_heard(lambda: prompt, question=q)
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
        here = self._place["index"] if self._place else -1
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
        visit = self._visit
        qid = question.id if question else None
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
                    turn.marks.append(item)
                    continue
                if isinstance(item, Draw):
                    uid = self.store.utter(self.id, visit, "drawn", item.code, question_id=qid)
                    self._drawings[f"d{uid}"] = _Drawing(uid, visit, item.code)
                    self._figure = {"id": f"d{uid}", "code": item.code}
                    await self._send({"type": "diagram", **self._figure})
                    continue
                text, audio = item
                if not audio:
                    # Shown, not heard. The turn carries on.
                    reason = "the speech engine returned no audio"
                    uid = self.store.utter(self.id, visit, "said", text, failed=reason, question_id=qid)
                    await self._send({"type": "unspoken", "id": uid, "text": text, "reason": reason})
                    continue
                playing = self.store.utter(self.id, visit, "said", text, question_id=qid)
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
            await self._cut_off(playing, unheard, items, ready, splitter, visit, qid)
        elif turn.failure is None:
            await self._set_state(LISTENING)
        return turn

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
