"""The lecture loop: one giving of a deck, paced the way the room hears it.

The lecture owns the loop and asks the host for one turn at a time. The host's
text is cut into sentences and drawings, each sentence is turned into audio ahead
of time, and everything is sent to the listeners at the moment it should be heard
or seen. The lecture, not any one page, decides what is playing now, so every
listener hears the same words and a late arrival sees the lecture as it is.

Whatever goes wrong is shown, and nothing that goes wrong ends the lecture:
a sentence that can't be spoken, a diagram that won't draw, a host that can't be
reached.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from professor import prompts, speech
from professor.hosts.base import Host
from professor.splitter import Draw, Say, Splitter
from professor.store import Slide, Store

Subscriber = Callable[[dict], Awaitable[None]]

# What the lecturer is doing right now. Listeners always see one of these.
READY, THINKING, SPEAKING, LISTENING, PAUSED, STUCK, ENDED = (
    "ready", "thinking", "speaking", "listening", "paused", "stuck", "ended")


@dataclass
class _HostFailed:
    reason: str


@dataclass
class _Drawing:
    utterance_id: int
    visit_id: int
    code: str
    error: str | None = None


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
        self._task: asyncio.Task | None = None
        self._instructed = False          # has this host session had the standing instructions?
        self._slide: Slide | None = None
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
            "floor": self._floor_open,
        }

    def _slide_message(self) -> dict:
        return {"type": "slide", "slide": {**self._slide.shown(), "revision": self._slide.revision_id},
                "place": self._place}

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
            await self._send({"type": "state", "state": state, "message": message})

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
        await self._send({"type": "state", "state": PAUSED, "message": ""})

    async def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        self._pausing.clear()
        self._running.set()
        await self._send({"type": "state", "state": self._state, "message": self._message})

    def retry(self) -> None:
        """Try the host again now, without waiting out the delay."""
        self._retry.set()

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
            slides = self.store.main_line(self.deck.id)
            for i, slide in enumerate(slides):
                await self._give(self.store.slide(slide.id), i, len(slides), how="start" if i == 0 else "next")
                await self._wait(self.pause_between_s)
            # Reaching the end of the main line doesn't end the lecture. It opens the floor.
            self._floor_open = True
            await self._send({"type": "floor"})
            await self._set_state(LISTENING, "End of the main line. The floor is open.")
        except Exception as exc:
            # A fault in the lecture itself. It can't go on, and the room is told why.
            await self._set_state(STUCK, f"The lecture has stopped: {exc or type(exc).__name__}")
            raise

    async def _give(self, slide: Slide, index: int, n: int, how: str) -> None:
        visit = self.store.arrive(self.id, slide, how)
        self._slide, self._figure = slide, None
        self._place = {"index": index, "of": n, "detour": False}
        await self._send(self._slide_message())

        await self._turn_until_heard(
            visit, lambda: prompts.present(self.deck, self._slide, index, n,
                                           self.store.utterances(self.id, visit)))

        # A drawing made during the turn may have failed on a page. The lecturer
        # gets one more turn to draw it again, or to carry on without it.
        mine = [d for d in self._drawings.values() if d.visit_id == visit]
        if mine and self._subs:
            await self._wait(self.draw_report_grace_s)  # the last drawing's report may still be on its way
        failed = [d for d in mine if d.error]
        if failed:
            await self._turn_until_heard(visit, lambda: prompts.redraw(failed[-1].code, failed[-1].error))
        self.store.mark_covered(visit)

    async def _turn_until_heard(self, visit: int, prompt: Callable[[], str]) -> None:
        """Run a turn, and keep trying while the host can't be reached."""
        attempt = 0
        while (failure := await self._turn(visit, prompt())) is not None:
            delay = self.retry_delays_s[attempt] if attempt < len(self.retry_delays_s) else None
            again = (f"Trying again in {delay:g} seconds." if delay is not None
                     else "Press Try again when it's back.")
            await self._set_state(STUCK, f"The lecturer can't be reached ({failure}). {again}")
            self._retry.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._retry.wait(), timeout=delay)
            attempt += 1

    async def _turn(self, visit: int, prompt: str) -> str | None:
        """One turn of the host, heard through to the end. Returns why the host
        failed, or None if it didn't."""
        if not self._instructed:
            prompt = prompts.standing(self.deck, self.store.main_line(self.deck.id)) + "\n\n" + prompt
        items: asyncio.Queue = asyncio.Queue()   # sentences and drawings, as written
        ready: asyncio.Queue = asyncio.Queue()   # the same, with audio, in the same order
        got_text = False

        async def read() -> None:
            nonlocal got_text
            splitter, failure = Splitter(), None
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
        failure = None
        try:
            while True:
                if ready.empty():
                    await self._set_state(THINKING)
                item = await ready.get()
                await self._running.wait()
                if item is None:
                    break
                if isinstance(item, _HostFailed):
                    failure = item.reason
                    break
                if isinstance(item, Draw):
                    uid = self.store.utter(self.id, visit, "drawn", item.code)
                    self._drawings[f"d{uid}"] = _Drawing(uid, visit, item.code)
                    self._figure = {"id": f"d{uid}", "code": item.code}
                    await self._send({"type": "diagram", **self._figure})
                    continue
                text, audio = item
                if not audio:
                    # Shown, not heard. The turn carries on.
                    reason = "the speech engine returned no audio"
                    uid = self.store.utter(self.id, visit, "said", text, failed=reason)
                    await self._send({"type": "unspoken", "id": uid, "text": text, "reason": reason})
                    continue
                uid = self.store.utter(self.id, visit, "said", text)
                seconds = speech.duration_s(audio)
                await self._set_state(SPEAKING)
                await self._send({"type": "sentence", "id": uid, "text": text, "seconds": round(seconds, 3),
                                  "audio": base64.b64encode(audio).decode()})
                await self._play(seconds)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if got_text:
            self._instructed = True
            self.store.set_host_session(self.id, self.host.session)
        if failure is None:
            await self._set_state(LISTENING)
        return failure

    async def _audio(self, text: str) -> bytes:
        """Audio for a sentence, trying once more if the engine comes back empty."""
        for _ in range(2):
            try:
                if audio := await self._speak(text):
                    return audio
            except Exception:
                pass
        return b""

    async def _play(self, seconds: float) -> None:
        """Wait while a clip plays. The clock stops while the lecture is paused."""
        while seconds > 0:
            await self._running.wait()
            began = time.monotonic()
            try:
                await asyncio.wait_for(self._pausing.wait(), timeout=seconds)
            except TimeoutError:
                return
            seconds -= time.monotonic() - began

    _wait = _play  # a silence the lecture chooses is paused the same way
