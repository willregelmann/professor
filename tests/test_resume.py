"""Stage 6: picking a lecture up where it stopped, and corrections kept against slides."""
import json

import pytest

from bench import listener
from professor import prompts
from professor.hosts.claude_sdk import ClaudeHost
from professor.lecture import Lecture
from professor.tools import Tools
from tests.test_hosts import StubClaude
from tests.test_lecture import SLIDE_1, SLIDE_2, SLIDE_3, Room
from tests.test_questions import ask_at

PUT_OFF = "Priya asks about the end. I will come to that on the last slide.\n```later\n3\n```"
BACK = "Coming back to Priya's question about the end. The last slide has just answered it."
LONG_2 = ("This is the second slide, and it starts here. This second sentence is where it stops. "
          "This third sentence is never reached.")


def leave_at(text):
    """Walk out, and take the server down, when this sentence arrives."""
    async def on_event(ws, event):
        if event["type"] == "sentence" and text in event["text"]:
            return "leave"
    return on_event


async def stopped_in_the_second_slide():
    """A lecture that put a question off on slide 1, then stopped partway through slide 2."""
    asker = ask_at(1, text="How does it end?")

    async def ask_then_leave(ws, event):
        await asker(ws, event)
        if event["type"] == "sentence" and "where it stops" in event["text"]:
            return "leave"

    room = Room([SLIDE_1, PUT_OFF, LONG_2])
    async with room:
        await room.attend(on_event=ask_then_leave)
    return room


async def test_a_lecture_is_picked_up_where_it_stopped_with_its_questions_intact():
    first = await stopped_in_the_second_slide()
    assert first.store.questions(first.lecture_id)[0].state == "put_off"
    before = first.store.path(first.lecture_id)

    resumed = "Picking up the second slide where we stopped. Here is the rest of it."
    async with Room([resumed, SLIDE_3, BACK], resume=first, resume_gap_s=30) as room:
        hello = None

        async def remember_hello(ws, event):
            nonlocal hello
        record = await listener.attend(room.url, timeout_s=20)
        hello, events = record["hello"], record["events"]

    # Before anyone presses Start, the page shows where the lecture stopped.
    assert hello["state"] == "ready" and hello["slide"]["title"] == "Two"
    assert hello["message"] == "This lecture stopped about 30 seconds ago. Press Start to pick it up."
    assert [(q["text"], q["state"], q["until"]) for q in hello["questions"]] == [
        ("How does it end?", "put_off", "slide 3")]

    # It resumes at slide 2, not from the top, and carries on to the end.
    assert [e["place"]["index"] for e in events if e["type"] == "slide"] == [1, 2]
    path = room.store.path(room.lecture_id)
    assert path[:len(before)] == before                          # what was kept is untouched
    assert [(v["how"], v["covered"]) for v in path[len(before):]] == [("resume", 1), ("next", 1)]
    assert room.store.main_line(room.deck.id)[0].id not in [v["slide_id"] for v in path[len(before):]]

    # The question put off before the stop is still open, and is come back to after its slide.
    q, = listener.questions(events)
    assert [state for state, _ in q["states"]] == ["answering", "answered"] and q["reply"].endswith(BACK)

    # The new lecturer starts from the standing instructions and the record, and is told
    # what the room had heard of the slide it stopped in.
    prompt = room.host.prompts[0]
    assert prompt.startswith("You are giving a live lecture")
    assert "RESUMING. This lecture was started earlier and stopped about 30 seconds ago." in prompt
    assert '- Slide "One": Welcome to the first slide of this lecture. It has two sentences to say.' in prompt
    assert '- Priya asked: "How does it end?" (put off) You said: Priya asks about the end.' in prompt
    assert "The gap was short. Do NOT recap" in prompt
    assert 'The last thing on the record is: "This second sentence is where it stops."' in prompt
    assert "do not repeat it. Pick up from where it stops:\nThis is the second slide, and it starts here." in prompt
    assert "never mid-sentence" in prompt and "RESUMING" not in room.host.prompts[1]


@pytest.mark.parametrize("gap_s, told, not_told", [
    (45, "Do NOT recap", "last time"),
    (20 * 60, "Begin with ONE sentence saying that you are picking the lecture back up", "last time"),
    (30 * 86400, "\"last time, we got as far as…\"", "Do NOT recap"),
])
async def test_the_recap_is_sized_to_the_gap(gap_s, told, not_told):
    first = await stopped_in_the_second_slide()
    lecture = Lecture(first.store, first.lecture_id, first.host, resume_gap_s=gap_s)
    note = lecture._resume.note
    assert told in note and not_told not in note
    assert f"stopped {prompts.ago(gap_s)} ago" in note
    assert "never claim to remember anything it does not show" in note


def test_a_gap_is_said_in_words():
    assert [prompts.ago(s) for s in (0.2, 3, 59, 90, 3600, 5 * 3600, 86400, 30 * 86400)] == [
        "about 1 second", "about 3 seconds", "about 59 seconds", "about 2 minutes", "about 1 hour", "about 5 hours",
        "about 1 day", "about 30 days"]


async def test_a_different_lecturer_can_pick_a_lecture_up():
    first = await stopped_in_the_second_slide()
    stub = StubClaude(["Carrying on with the second slide from where it stopped.", SLIDE_3, BACK])
    async with Room(None, host=ClaudeHost(client=lambda: stub), resume=first, resume_gap_s=30) as room:
        events = await room.attend()
    assert [e["place"]["index"] for e in events if e["type"] == "slide"] == [1, 2]
    assert room.store.questions(room.lecture_id)[0].state == "answered"
    assert "THE RECORD SO FAR" in stub.queries[0]          # what it knows of the lecture came with the lecture
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 0, 1, 1]


async def test_a_lecture_that_stopped_before_a_slide_was_begun_starts_that_slide_whole():
    async def leave_as_the_second_slide_comes_up(ws, event):
        if event["type"] == "slide" and event["place"]["index"] == 1:
            return "leave"

    room = Room([SLIDE_1, SLIDE_2])
    async with room:
        await room.attend(on_event=leave_as_the_second_slide_comes_up)
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 0]
    async with Room([SLIDE_2, SLIDE_3], resume=room, resume_gap_s=10) as again:
        events = await again.attend()
    assert [e["place"]["index"] for e in events if e["type"] == "slide"] == [1, 2]
    assert "cut off partway" not in again.host.prompts[0]          # nothing of it had been heard
    assert "Pick up from where it stops" not in again.host.prompts[0]


async def test_a_lecture_can_stop_and_be_picked_up_more_than_once():
    first = await stopped_in_the_second_slide()
    second = Room(["Second try at the second slide. It stops again right here. And this is not reached."],
                  resume=first, resume_gap_s=10)
    async with second:
        await second.attend(on_event=leave_at("stops again right here"))
    async with Room(["Third time lucky.", SLIDE_3, BACK], resume=second, resume_gap_s=10) as third:
        events = await third.attend()
    prompt = third.host.prompts[0]
    # Everything the room heard of the slide, across both stops, is handed over.
    assert "This is the second slide, and it starts here." in prompt.split("Pick up from where it stops:")[1]
    assert "Second try at the second slide." in prompt.split("Pick up from where it stops:")[1]
    assert [e["place"]["index"] for e in events if e["type"] == "slide"] == [1, 2]


async def test_a_question_being_answered_when_the_lecture_stopped_is_heard_again():
    asker = ask_at(1, text="Why two sentences?")

    async def ask_then_leave(ws, event):
        await asker(ws, event)
        if event["type"] == "question" and event["question"]["state"] == "answering":
            return "leave"

    first = Room([SLIDE_1, "Priya asks why. This answer is never finished because the server goes away."])
    async with first:
        await first.attend(on_event=ask_then_leave)
    assert first.store.questions(first.lecture_id)[0].state == "answering"

    async with Room(["Priya asked why there are two sentences. Because one would be too few.", SLIDE_2, SLIDE_3],
                    resume=first, resume_gap_s=10) as room:
        hello = (await listener.attend(room.url, timeout_s=20))
    assert hello["hello"]["questions"][0]["state"] == "heard"       # not lost, and not shown as being answered
    assert room.store.questions(room.lecture_id)[0].state == "answered"
    assert room.store.reply(room.store.questions(room.lecture_id)[0].id).endswith("Because one would be too few.")


async def test_a_lecture_that_had_reached_the_end_resumes_with_the_floor_open():
    first = Room([SLIDE_1, SLIDE_2, SLIDE_3])
    async with first:
        await first.attend()
    async with Room(["We are back, and the floor is open for questions."], resume=first, resume_gap_s=10) as room:
        events = await room.attend()
    assert events[-1]["message"] == "End of the main line. The floor is open."
    assert "had already reached the end of its slides" in room.host.prompts[0]
    assert [v["how"] for v in room.store.path(room.lecture_id)] == ["start", "next", "next", "resume"]


async def test_a_deck_that_changed_while_the_lecture_was_stopped_is_said_so():
    first = await stopped_in_the_second_slide()
    store, deck = first.store, first.deck
    one, two, three = store.main_line(deck.id)
    store.revise_slide(one.id, title="One, revised", notes="new notes")
    extra = store.add_slide(deck.id, title="Four", notes="notes")
    store.arrange(deck.id, [one.id, three.id, extra.id])          # the slide it stopped on is taken out

    lecture = Lecture(store, first.lecture_id, first.host, resume_gap_s=3600)
    note = lecture._resume.note
    assert "THE DECK HAS CHANGED" in note
    assert ('The slide the lecture stopped on, "Two", has been taken out of the deck, so the lecture resumes at '
            'the nearest point that is left: slide 2, "Three".') in note
    assert 'The slide "One, revised", which was already given, has been revised since.' in note
    assert 'A slide has been added: "Four".' in note
    assert lecture._resume.index == 1 and lecture._resume.carry == []
    assert lecture.snapshot()["slide"]["title"] == "Three"


async def test_an_ended_lecture_cannot_be_picked_up():
    first = Room([SLIDE_1, SLIDE_2, SLIDE_3])
    async with first:
        await first.attend()
        await first.lecture.end()
    with pytest.raises(ValueError, match="has ended. Giving the deck again is a new lecture."):
        Lecture(first.store, first.lecture_id, first.host)


async def test_a_long_pause_is_followed_by_a_sentence_picking_the_thread_up(monkeypatch):
    import professor.lecture as lecture_module
    monkeypatch.setattr(lecture_module, "LONG_PAUSE_S", 0.3)
    paused = False

    async def pause_a_while(ws, event):
        nonlocal paused
        import asyncio
        if event["type"] == "sentence" and not paused:
            paused = True
            await ws.send_json({"cmd": "pause"})
            await asyncio.sleep(0.5)
            await ws.send_json({"cmd": "resume"})

    async with Room([SLIDE_1, SLIDE_2, SLIDE_3]) as room:
        await room.attend(on_event=pause_a_while)
    assert room.host.prompts[1].startswith("The lecture was paused for about")
    assert "The lecture was paused" not in room.host.prompts[2]


async def test_a_correction_is_kept_against_the_slide_the_question_was_about():
    wrong = ("Priya asks if it is really two sentences. Let me check. She is right to ask: I said two, and it is "
             "three.\n```correct\nThe notes say two sentences; the slide has three.\n```\nThank you, Priya.")
    async with Room([SLIDE_1, wrong, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(on_event=ask_at(2, text="Is it really two sentences?"))
        store = room.store
        one = store.main_line(room.deck.id)[0]
        kept, = store.corrections(slide_id=one.id)
        tools = Tools(store)

        # The question was asked during slide 1 and taken after it, so that's the slide it's kept against.
        assert (kept.text, kept.state) == ("The notes say two sentences; the slide has three.", "open")
        assert kept.question_id == store.questions(room.lecture_id)[0].id and kept.lecture_id == room.lecture_id
        assert listener.questions(events)[0]["reply"].endswith("Thank you, Priya.")
        assert [e["question"]["corrections"] for e in events if e["type"] == "question"][-1] == [kept.text]

        # Whoever revises the deck sees it, deals with it, and says so. It is never lost.
        deck = json.loads(tools.call("deck", {"deck": room.deck.id})[0])
        assert deck["main_line"][0]["open_corrections"] == 1
        slide = json.loads(tools.call("slide", {"slide": one.id})[0])
        assert slide["corrections"] == [{"correction": kept.id, "text": kept.text, "state": "open", "why": "",
                                         "from_lecture": room.lecture_id,
                                         "after_question": "Is it really two sentences?"}]
        tools.call("write_slide", {"slide": one.id, "notes": "It has three sentences to say."})
        text, failed = tools.call("correct", {"correction": kept.id, "settle": "set_aside"})
        assert failed and "needs the reason why" in text
        slide = json.loads(tools.call("correct", {"correction": kept.id, "settle": "applied",
                                                  "why": "The notes now say three."})[0])
        assert slide["corrections"][0]["state"] == "applied" and slide["notes"] == "It has three sentences to say."
        assert "open_corrections" not in json.loads(tools.call("deck", {"deck": room.deck.id})[0])["main_line"][0]
        # A correction can also be kept while preparing, with no lecture behind it.
        slide = json.loads(tools.call("correct", {"slide": one.id, "text": "The title is too vague."})[0])
        assert [c["state"] for c in slide["corrections"]] == ["applied", "open"]
