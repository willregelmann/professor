"""Stage 1's exit criteria, against the fake host: a headless listener sits through
lectures on the real server and everything is judged from what it was sent."""
import asyncio
import json

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from bench import listener, metrics
from professor.hosts.fake import FakeHost
from professor.lecture import Lecture
from professor.server import make_app
from professor.store import Store

CLIP = b"\x00" * 900  # 0.15 seconds of 48 kbit/s audio

SLIDE_1 = "Welcome to the first slide of this lecture. It has two sentences to say."
SLIDE_2 = ("Here is how the pieces fit together.\n```mermaid\nflowchart LR\n  A[\"one\"] --> B[\"two\"]\n```\n"
           "As you can see, one leads to two.")
SLIDE_3 = "This is the last slide of the deck. After it the floor is open."


class Room:
    """A deck, a lecture on the real server, and a way to sit in on it."""

    def __init__(self, replies, *, speak=None, slides=None, host=None, heartbeat_s=30, **lecture_options):
        self.store = Store()
        self.deck = self.store.create_deck("How it fits together", audience="the test suite", minutes=1)
        for kwargs in slides or [{"title": "One"}, {"title": "Two"}, {"title": "Three"}]:
            self.store.add_slide(self.deck.id, **{"notes": "notes", **kwargs})
        self.host = host or FakeHost(replies, chars_per_s=4000)
        self.spoken: list[str] = []

        async def default_speak(text):
            self.spoken.append(text)
            return CLIP

        options = {"pause_between_s": 0.05, "retry_delays_s": (0.05, 0.05), "draw_report_grace_s": 0.1,
                   "text_timeout_s": 2.0, **lecture_options}
        self.lecture_id = self.store.create_lecture(self.deck.id, "fake")
        self.lecture = Lecture(self.store, self.lecture_id, self.host, speak=speak or default_speak, **options)
        self.server = TestServer(make_app(self.lecture, heartbeat_s=heartbeat_s))

    async def __aenter__(self):
        await self.server.start_server()
        self.url = str(self.server.make_url("/ws"))
        return self

    async def __aexit__(self, *exc):
        await self.server.close()

    async def attend(self, **kwargs):
        return (await listener.attend(self.url, timeout_s=20, **kwargs))["events"]

    def said(self):
        return [(u.kind, u.text, u.failed) for u in self.store.utterances(self.lecture_id)]


def kinds(events, *types):
    return [e["type"] if e["type"] != "state" else e["state"] for e in events if e["type"] in types]


async def test_a_three_slide_deck_plays_end_to_end():
    async with Room([SLIDE_1, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend()

    turns = listener.slide_turns(events)
    assert [t["slide"] for t in turns] == [0, 1, 2]
    assert [e["place"] for e in events if e["type"] == "slide"][1] == {"index": 1, "of": 3, "detour": False}
    # The drawing lands between the two sentences it was written between.
    assert turns[1]["seq"] == [["SAY", "Here is how the pieces fit together."],
                               ["DRAW", "flowchart LR\n  A[\"one\"] --> B[\"two\"]"],
                               ["SAY", "As you can see, one leads to two."]]
    assert [t["sentences"] for t in turns] == [2, 2, 2] and not any(t["warnings"] for t in turns)

    # Reaching the end of the main line opens the floor; it doesn't end the lecture.
    assert events[-1] == {"type": "state", "state": "listening",
                          "message": "End of the main line. The floor is open.", "t": events[-1]["t"]}
    assert room.store.lecture(room.lecture_id)["state"] == "listening"

    # The lecture kept its path and its words.
    assert [(v["how"], v["covered"]) for v in room.store.path(room.lecture_id)] == [
        ("start", 1), ("next", 1), ("next", 1)]
    assert [text for kind, text, _ in room.said() if kind == "said"] == room.spoken
    assert room.store.lecture(room.lecture_id)["host_session"] == "fake-session"

    # The standing instructions go once, at the start of the host's session.
    assert room.host.prompts[0].startswith("You are giving a live lecture, out loud, to the test suite.")
    assert "Present slide 1 of 3" in room.host.prompts[0]
    assert room.host.prompts[1].startswith("Present slide 2 of 3")

    assert metrics.unexplained(events) == []


async def test_a_sentence_that_cannot_be_spoken_is_shown_and_the_lecture_carries_on():
    calls: list[str] = []

    async def speak(text):
        calls.append(text)
        return b"" if "two sentences" in text else CLIP

    async with Room([SLIDE_1, SLIDE_2, SLIDE_3], speak=speak) as room:
        events = await room.attend()

    lost = "It has two sentences to say."
    assert [e["text"] for e in events if e["type"] == "unspoken"] == [lost]
    assert calls.count(lost) == 2                               # tried once more before giving up
    assert ("said", lost, "the speech engine returned no audio") in room.said()
    assert not any(e["type"] == "sentence" and not e["bytes"] for e in events)  # no empty clip is ever sent
    assert [t["sentences"] for t in listener.slide_turns(events)] == [1, 2, 2]
    assert metrics.unexplained(events) == []


async def test_a_sentence_that_fails_once_is_spoken_on_the_second_try():
    failed_once: set[str] = set()

    async def speak(text):
        if text not in failed_once:
            failed_once.add(text)
            raise RuntimeError("engine hiccup")
        return CLIP

    async with Room([SLIDE_1, SLIDE_2, SLIDE_3], speak=speak) as room:
        events = await room.attend()
    assert not [e for e in events if e["type"] == "unspoken"]
    assert [t["sentences"] for t in listener.slide_turns(events)] == [2, 2, 2]


async def test_a_diagram_that_will_not_draw_is_shown_reported_and_drawn_again():
    broken = "Here is the picture.\n```mermaid\nflowchart LR\n  A[broken -->\n```\nThat is the idea."
    redrawn = "Let me try that picture again.\n```mermaid\nflowchart LR\n  A --> B\n```"
    async with Room([broken, redrawn, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(draw_fails=lambda code: "Parse error on line 2" if "broken" in code else None)

    first = listener.slide_turns(events)[0]
    # The room is told, once; the report races the next sentence, so its place isn't fixed.
    assert first["seq"].count(["WARN", "A diagram couldn't be drawn."]) == 1
    assert [kind for kind, _ in first["seq"] if kind != "WARN"] == ["SAY", "DRAW", "SAY", "SAY", "DRAW"]
    # The lecturer was told what the room saw, once, and the lecture went on.
    assert "could not be shown" in room.host.prompts[1] and "Parse error on line 2" in room.host.prompts[1]
    assert len(room.host.prompts) == 4
    assert ("drawn", "flowchart LR\n  A[broken -->", "Parse error on line 2") in room.said()
    assert ("drawn", "flowchart LR\n  A --> B", None) in room.said()
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 1, 1]
    assert metrics.unexplained(events) == []


async def test_a_redrawn_diagram_that_fails_again_is_not_tried_a_third_time():
    broken = "Here is the picture.\n```mermaid\nbroken\n```\nThat is the idea."
    async with Room([broken, broken, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(draw_fails=lambda code: "bad" if "broken" in code else None)
    assert len(room.host.prompts) == 4
    assert [t["slide"] for t in listener.slide_turns(events)] == [0, 1, 2]


async def test_a_prepared_slide_whose_diagram_will_not_draw_says_so():
    slides = [{"title": "One", "diagram": "flowchart broken"}, {"title": "Two"}]
    async with Room([SLIDE_1, SLIDE_3], slides=slides) as room:
        events = await room.attend(draw_fails=lambda code: "Parse error" if "broken" in code else None)
        assert room.store.main_line(room.deck.id)[0].show_error == "Parse error"
    assert ["WARN", "This slide's diagram couldn't be drawn."] in listener.slide_turns(events)[0]["seq"]
    assert [t["slide"] for t in listener.slide_turns(events)] == [0, 1]


async def test_a_host_that_cannot_be_reached_is_shown_and_the_lecture_picks_up_when_it_is_back():
    # The second slide's turn is refused twice, then goes through.
    host = FakeHost([SLIDE_1, SLIDE_2, SLIDE_3], chars_per_s=4000, failures={1: 0, 2: 0})
    async with Room(None, host=host) as room:
        events = await room.attend()

    stuck = [e for e in events if e["type"] == "state" and e["state"] == "stuck"]
    assert len(stuck) == 2
    assert stuck[0]["message"] == ("The lecturer can't be reached (connection refused). "
                                   "Trying again in 0.05 seconds.")
    turns = listener.slide_turns(events)
    assert [t["slide"] for t in turns] == [0, 1, 2] and [t["sentences"] for t in turns] == [2, 2, 2]
    assert turns[1]["stuck"] == 2
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 1, 1]
    assert metrics.unexplained(events) == []


async def test_a_host_lost_mid_slide_is_told_what_the_room_already_heard():
    host = FakeHost([SLIDE_1, SLIDE_2, SLIDE_3], chars_per_s=4000, failures={0: 60})
    async with Room(None, host=host) as room:
        events = await room.attend()

    first = listener.slide_turns(events)[0]
    assert first["stuck"] == 1
    # Only whole sentences were said before the connection went; half a sentence isn't spoken.
    assert first["seq"][0] == ["SAY", "Welcome to the first slide of this lecture."]
    assert "The room has already heard this" in host.prompts[1]
    assert "Welcome to the first slide of this lecture." in host.prompts[1].split("already heard this")[1]
    # It got some words out the first time, so its session has the standing instructions already.
    assert not host.prompts[1].startswith("You are giving a live lecture")
    assert [t["slide"] for t in listener.slide_turns(events)] == [0, 1, 2]


async def test_a_host_that_never_answers_leaves_the_lecture_stuck_but_not_dead():
    host = FakeHost([SLIDE_1], chars_per_s=4000, first_delay_s=30)
    async with Room(None, host=host, text_timeout_s=0.1, retry_delays_s=()) as room:
        async def leave_when_stuck(ws, event):
            if event["type"] == "state" and event["state"] == "stuck":
                return "leave"
        events = await room.attend(on_event=leave_when_stuck)
        assert events[-1]["message"] == ("The lecturer can't be reached (no word from it in 0.1 seconds). "
                                         "Press Try again when it's back.")
        assert kinds(events, "state") == ["thinking", "stuck"]

        # The server still answers. Someone arriving now sees a lecture that can't go on,
        # and asking it to try again does try again.
        async with aiohttp.ClientSession() as http, http.ws_connect(room.url) as ws:
            hello = json.loads((await ws.receive()).data)
            assert hello["state"] == "stuck" and hello["slide"]["title"] == "One"
            await ws.send_json({"cmd": "retry"})
            assert [json.loads((await ws.receive()).data)["state"] for _ in range(2)] == ["thinking", "stuck"]
        assert len(host.prompts) == 2


async def test_pausing_stops_the_lecture_for_everyone_and_loses_nothing():
    arrived: list[tuple[str, float]] = []
    paused_at = resumed_at = None

    async def pause_after_the_first_sentence(ws, event):
        nonlocal paused_at, resumed_at
        now = asyncio.get_running_loop().time()
        if event["type"] == "sentence":
            arrived.append((event["text"], now))
            if len(arrived) == 1:
                await ws.send_json({"cmd": "pause"})
        if event["type"] == "state" and event["state"] == "paused":
            paused_at = now
            await asyncio.sleep(0.6)
            resumed_at = asyncio.get_running_loop().time()
            await ws.send_json({"cmd": "resume"})

    async with Room([SLIDE_1, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(on_event=pause_after_the_first_sentence)

    states = kinds(events, "state")
    assert states[:4] == ["thinking", "speaking", "paused", "speaking"]
    # Nothing was said while it was paused, and everything was said afterwards.
    assert not [text for text, t in arrived if paused_at < t < resumed_at]
    assert [t["sentences"] for t in listener.slide_turns(events)] == [2, 2, 2]


async def test_someone_who_arrives_late_sees_the_lecture_as_it_is():
    async with Room([SLIDE_1, SLIDE_2, SLIDE_3]) as room:
        late = {}

        async def look_in_during_the_second_slide(ws, event):
            if event["type"] == "diagram" and not late:
                async with aiohttp.ClientSession() as http, http.ws_connect(room.url) as other:
                    late.update(json.loads((await other.receive()).data))

        await room.attend(on_event=look_in_during_the_second_slide)

    assert late["type"] == "hello" and late["deck"]["subject"] == "How it fits together"
    assert late["slide"]["title"] == "Two" and late["place"] == {"index": 1, "of": 3, "detour": False}
    assert late["diagram"]["code"].startswith("flowchart LR")
    assert late["state"] in ("speaking", "thinking") and late["floor"] is False
    assert "notes" not in json.dumps(late)


async def test_a_fault_in_the_lecture_itself_is_shown_not_swallowed():
    class BrokenStore(Store):
        def arrive(self, *args):
            raise RuntimeError("disk full")

    async with Room([SLIDE_1]) as room:
        room.lecture.store.__class__ = BrokenStore

        async def leave_when_stuck(ws, event):
            if event["type"] == "state" and event["state"] == "stuck":
                return "leave"
        events = await room.attend(on_event=leave_when_stuck)
    assert events[-1]["message"] == "The lecture has stopped: disk full"


async def test_ending_a_lecture_is_shown_and_kept():
    async with Room([SLIDE_1, SLIDE_2, SLIDE_3]) as room:
        async def end_at_the_second_slide(ws, event):
            if event["type"] == "slide" and event["place"]["index"] == 1:
                await ws.send_json({"cmd": "end"})
        events = await room.attend(on_event=end_at_the_second_slide)
    assert events[-1]["state"] == "ended"
    assert room.store.lecture(room.lecture_id)["ended_at"]
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 0]


@pytest.mark.parametrize("message", ["not json", '{"cmd": "dance"}', '{"cmd": "draw_failed"}'])
async def test_something_the_server_cannot_read_gets_an_answer_and_harms_nothing(message):
    async with Room([SLIDE_1]) as room:
        async with aiohttp.ClientSession() as http, http.ws_connect(room.url) as ws:
            await ws.receive()
            await ws.send_str(message)
            assert json.loads((await ws.receive()).data)["type"] == "warn"


async def test_a_browser_that_has_sat_idle_through_a_heartbeat_can_still_press_start():
    # Browsers offer compression on every websocket. With aiohttp 3.14 that and a
    # heartbeat together broke the first thing a listener did after waiting a while.
    async with Room([SLIDE_1], heartbeat_s=0.2) as room:
        async with aiohttp.ClientSession() as http, http.ws_connect(room.url, compress=15) as ws:
            seen = []

            async def read():
                async for m in ws:
                    seen.append(json.loads(m.data))
            reader = asyncio.create_task(read())
            await asyncio.sleep(0.7)
            await ws.send_json({"cmd": "start"})
            await asyncio.sleep(0.5)
            reader.cancel()
    assert [m["type"] for m in seen][:3] == ["hello", "slide", "state"]
