"""Stage 4: moving around a deck, and slides made up on the spot. A scripted
lecturer moves the lecture, against the real server, with a headless listener."""
import asyncio
import json

import aiohttp

from bench import listener
from professor.showcheck import Check
from professor.tools import Tools
from tests.test_lecture import SLIDE_1, SLIDE_2, SLIDE_3, Room
from tests.test_questions import ask_at

DETOURS = (
    "Priya wants the first slide again. Let me go back to slide one.\n```go\n1\n```\n"
    "Here is the first slide again, as promised. Now let me put a sketch on the screen as well.\n"
    "```slide\ntitle: A sketch of the idea\ndiagram:\nflowchart LR\n  A --> B\ntext: One leads to two.\n```\n"
    "The sketch shows one leading to two. Back to slide one for a moment.\n```go\nreturn\n```\n"
    "That was all I wanted from the first slide. And now back to where we were.\n```go\nreturn\n```\n"
    "We are back on slide two, where we left off.")


def checked(diagram="", math=""):
    return Check(checked=True, error="Parse error" if "broken" in diagram else None)


def places(events):
    """Where the lecture was each time the screen changed."""
    out = []
    for e in events:
        if e["type"] == "slide":
            p = e["place"]
            where = "made up" if p.get("made_up") else p["index"] + 1
            out.append((where, p["detour"]))
    return out


def said_before_each_move(events):
    """For every move that isn't simply "next": was something said since the screen last changed?"""
    out, spoke, last = [], False, None
    for e in events:
        if e["type"] == "sentence":
            spoke = True
        elif e["type"] == "slide":
            p = e["place"]
            simply_next = not p["detour"] and last is not None and not last["detour"] and p["index"] == last["index"] + 1
            if last is not None and not simply_next:
                out.append(spoke)
            spoke, last = False, p
    return out


async def test_a_detour_inside_a_detour_comes_back_to_the_right_slide_each_time():
    async with Room([SLIDE_1, SLIDE_2, DETOURS, SLIDE_3], check=checked) as room:
        before = [s.id for s in room.store.main_line(room.deck.id)]
        events = await room.attend(on_event=ask_at(3, text="Can we see the first slide again?"))
        after = [s.id for s in room.store.main_line(room.deck.id)]
        beside = room.store.beside(room.deck.id)
        question, = room.store.questions(room.lecture_id)

    # Slide 1, slide 2, off to slide 1, further off to a made-up slide, back to slide 1,
    # back to slide 2, and on to slide 3.
    assert places(events) == [(1, False), (2, False), (1, 1), ("made up", 2), (1, 1), (2, False), (3, False)]
    assert [v["how"] for v in room.store.path(room.lecture_id)] == [
        "start", "next", "detour", "made up", "return", "return", "next"]
    detour = [e["place"] for e in events if e["type"] == "slide"][2]
    assert detour == {"index": 0, "of": 3, "detour": 1, "from": 1}

    # Every move that wasn't simply "next" had something said before it.
    assert said_before_each_move(events) == [True, True, True, True]

    # Moving through a deck never changes its order. The made-up slide is kept beside it,
    # with the slide the lecture was on and the question it was made for.
    assert after == before
    made, = beside
    assert (made.title, made.origin, made.position) == ("A sketch of the idea", "made_up", None)
    assert (made.anchor_slide_id, made.question_id) == (before[1], question.id)
    assert made.diagram == "flowchart LR\n  A --> B" and made.text == "One leads to two." and made.show_checked
    assert "Can we see the first slide again?" in made.notes

    # What was said is kept with the slide it was said on, and all of it is the reply.
    by_slide = {}
    for u in room.store.utterances(room.lecture_id):
        if u.question_id:
            slide_id = room.store.db.execute(
                "SELECT r.slide_id FROM visits v JOIN slide_revisions r ON r.id = v.slide_revision_id "
                "WHERE v.id = ?", (u.visit_id,)).fetchone()[0]
            by_slide.setdefault(slide_id, []).append(u.text)
    assert by_slide[made.id] == ["The sketch shows one leading to two.", "Back to slide one for a moment."]
    assert by_slide[before[1]][-1] == "We are back on slide two, where we left off."
    q = listener.questions(events)[0]
    assert q["state"] == "answered" and q["slides"] == ["A sketch of the idea"]
    assert [v["covered"] for v in room.store.path(room.lecture_id) if v["how"] in ("start", "next")] == [1, 1, 1]


async def test_a_detour_the_lecturer_forgets_to_come_back_from_is_closed_when_its_turn_ends():
    wander = ("Let me show the first slide.\n```go\n1\n```\nAnd a sketch on top of that.\n"
              "```mermaid\nflowchart LR\n  A --> B\n```\nThat is the sketch, and that is all.")
    async with Room([SLIDE_1, SLIDE_2, wander, "Back to slide two now.", SLIDE_3], check=checked) as room:
        events = await room.attend(on_event=ask_at(3, text="Show me the first slide and a sketch?"))
        made, = room.store.beside(room.deck.id)
    assert places(events) == [(1, False), (2, False), (1, 1), ("made up", 2), (2, False), (3, False)]
    assert [v["how"] for v in room.store.path(room.lecture_id)][2:5] == ["detour", "made up", "return"]
    # The lecturer never said it was going back, so it's asked to, and the room hears
    # where the screen is going before it goes.
    assert room.host.prompts[3].startswith("Your turn ended with a detour still on the screen.")
    assert 'going back to slide 2, "Two"' in room.host.prompts[3]
    assert said_before_each_move(events) == [True, True, True]
    order = [e["text"] if e["type"] == "sentence" else e["place"]["detour"] for e in events
             if e["type"] == "sentence" or e["type"] == "slide"]
    assert order[order.index("Back to slide two now.") + 1] is False     # said, and only then the screen moves
    assert listener.questions(events)[0]["reply"].endswith("Back to slide two now.")
    # A plain diagram drawn while answering is kept as a slide too, under the question.
    assert made.title == "Show me the first slide and a sketch?" and made.origin == "made_up"


async def test_a_diagram_drawn_while_presenting_a_slide_stays_on_that_slide():
    async with Room([SLIDE_1, SLIDE_2, SLIDE_3], check=checked) as room:
        events = await room.attend()
        assert room.store.beside(room.deck.id) == []
    assert places(events) == [(1, False), (2, False), (3, False)]
    assert [e["type"] for e in events if e["type"] == "diagram"] == ["diagram"]


async def test_the_room_can_have_the_rest_of_a_slide_skipped():
    long_slide = ("This is the first sentence of a long slide. This is the second sentence, which gets cut off. "
                  "This third sentence is never reached at all.")

    async def stop_then_ask_to_skip(ws, event):
        if event["type"] == "sentence" and "second sentence" in event["text"]:
            await ws.send_json({"cmd": "stop"})
        if event["type"] == "stopped":
            await ws.send_json({"cmd": "ask", "who": "Priya", "text": "Skip this slide, please."})

    async with Room([long_slide, "Of course, Priya. Moving on to the next slide.\n```go\nnext\n```", SLIDE_2, SLIDE_3],
                    check=checked) as room:
        events = await room.attend(on_event=stop_then_ask_to_skip)
        read = json.loads(Tools(room.store).call("lecture", {})[0])
    assert places(events) == [(1, False), (2, False), (3, False)]
    assert len(room.host.prompts) == 4                       # the first slide was not picked back up
    assert [(v["how"], v["covered"]) for v in room.store.path(room.lecture_id)] == [
        ("start", 0), ("next", 1), ("next", 1)]
    assert [s["title"] for s in read["not_covered"]] == ["One"]


async def test_the_lecture_can_carry_on_from_a_later_slide_and_knows_what_it_skipped():
    skip = "Priya wants only the last slide. Then let us go straight to slide three.\n```go\nfrom 3\n```"
    async with Room([SLIDE_1, skip, SLIDE_3], check=checked) as room:
        events = await room.attend(on_event=ask_at(1, text="Just the last slide, please."))
        read = json.loads(Tools(room.store).call("lecture", {})[0])
    assert places(events) == [(1, False), (3, False)]
    assert [v["how"] for v in room.store.path(room.lecture_id)] == ["start", "jump"]
    assert [s["title"] for s in read["not_covered"]] == ["Two"]
    assert said_before_each_move(events) == [True]


async def test_going_back_to_a_slide_already_given_is_not_giving_it_again():
    back = "Priya wants the first slide once more. Let us take it again from slide one.\n```go\nfrom 1\n```"
    again = "We are back on the first slide. Briefly this time: it has two sentences."
    async with Room([SLIDE_1, SLIDE_2, back, again, SLIDE_2, SLIDE_3], check=checked) as room:
        events = await room.attend(on_event=ask_at(3, text="Can we do the first slide again?"))
        before = [s.id for s in room.store.main_line(room.deck.id)]
    assert places(events) == [(1, False), (2, False), (1, False), (2, False), (3, False)]
    assert [v["how"] for v in room.store.path(room.lecture_id)] == ["start", "next", "jump", "next", "next"]
    # The lecturer is told it has been here, and what it said then.
    prompt = room.host.prompts[3]
    assert "ALREADY given this slide" in prompt
    assert prompt.rstrip().endswith("Welcome to the first slide of this lecture. It has two sentences to say.")
    assert "ALREADY given this slide" not in room.host.prompts[0]
    assert len(before) == 3


async def test_a_made_up_slide_that_cannot_be_drawn_is_not_shown_and_is_drawn_again():
    bad = ("Priya asks for a picture. Let me put one on the screen.\n"
           "```slide\ntitle: The picture\ndiagram:\nflowchart broken\n```\nThat should be it.")
    good = ("Let me try that picture again.\n```slide\ntitle: The picture\ndiagram:\nflowchart LR\n  A --> B\n```\n"
            "There it is. Back to slide one.\n```go\nreturn\n```")
    async with Room([SLIDE_1, bad, good, SLIDE_2, SLIDE_3], check=checked) as room:
        events = await room.attend(on_event=ask_at(1, text="Can I have a picture?"),
                                   draw_fails=lambda code: "never sent to a page" if "broken" in code else None)
        made = room.store.beside(room.deck.id)
    # Only the one that could be drawn was ever shown or kept.
    assert places(events) == [(1, False), ("made up", 1), (1, False), (2, False), (3, False)]
    assert [m.diagram for m in made] == ["flowchart LR\n  A --> B"]
    assert [e["message"] for e in events if e["type"] == "warn"] == ["A diagram couldn't be drawn."]
    assert "could not be shown" in room.host.prompts[2] and "Parse error" in room.host.prompts[2]
    assert listener.questions(events)[0]["state"] == "answered"


async def test_a_move_to_a_slide_that_is_not_there_is_shown_as_a_failure():
    lost = "Let me show you slide nine.\n```go\n9\n```\nIt seems that slide is not there."
    async with Room([SLIDE_1, lost, SLIDE_2, SLIDE_3], check=checked) as room:
        events = await room.attend(on_event=ask_at(1, text="Show me slide nine?"))
    assert [e["message"] for e in events if e["type"] == "warn"] == [
        "The lecturer tried to move to a slide that isn't there."]
    assert places(events) == [(1, False), (2, False), (3, False)]


async def test_at_the_end_of_the_main_line_next_goes_nowhere_and_the_lecture_can_go_back_in():
    asked = []

    async def at_the_floor(ws, event):
        if event["type"] == "state" and event["message"].startswith("End of the main line"):
            if len(asked) == 0:
                asked.append("next")
                await ws.send_json({"cmd": "ask", "who": "Priya", "text": "What comes next?"})
            elif len(asked) == 1:
                asked.append("back")
                await ws.send_json({"cmd": "ask", "who": "Priya", "text": "The last slide again, please."})
            else:
                return "leave"

    replies = [SLIDE_1, SLIDE_2, SLIDE_3, "Priya asks what is next. Nothing is: that was the last slide.\n```go\nnext\n```",
               "Of course. Let us take the last slide again.\n```go\nfrom 3\n```", "Back on the last slide, briefly."]
    async with Room(replies, check=checked) as room:
        record = await listener.attend(room.url, timeout_s=20, on_event=at_the_floor, stay=True)
    events = record["events"]
    assert places(events) == [(1, False), (2, False), (3, False), (3, False)]
    assert sum(1 for e in events if e["type"] == "floor") == 2          # it opened, closed, and opened again
    assert all(q["state"] == "answered" for q in listener.questions(events))


async def test_someone_who_arrives_during_a_detour_can_tell_it_is_one():
    wander = ("Let me show the first slide.\n```go\n1\n```\nHere it is, the first slide again. Take a look at it. "
              "Back to slide two.\n```go\nreturn\n```")
    async with Room([SLIDE_1, SLIDE_2, wander, SLIDE_3], check=checked) as room:
        late = {}

        async def look_in(ws, event):
            if event["type"] == "slide" and event["place"]["detour"] and not late:
                await asyncio.sleep(0.05)
                async with aiohttp.ClientSession() as http, http.ws_connect(room.url) as other:
                    late.update(json.loads((await other.receive()).data))
        await room.attend(on_event=lambda ws, e: ask_and_look(ws, e, look_in))
    assert late["slide"]["title"] == "One" and late["place"] == {"index": 0, "of": 3, "detour": 1, "from": 1}


_asker = None


async def ask_and_look(ws, event, look_in):
    global _asker
    if _asker is None or event["type"] == "hello":
        _asker = ask_at(3, text="First slide again?")
    await _asker(ws, event)
    await look_in(ws, event)
