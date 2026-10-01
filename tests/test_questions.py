"""Stage 2: what the room says back. A headless listener asks, stops and carries
on, against the real server and a scripted lecturer."""
import asyncio
import json

import aiohttp

from bench import listener
from professor.hosts.fake import FakeHost
from tests.test_lecture import SLIDE_1, SLIDE_2, SLIDE_3, Room

ANSWER = "Priya asks why there are two sentences. There are two because one would be too few."
PUT_OFF = "Priya asks about the end. I will come to that on the last slide.\n```later\n3\n```"
BACK = "Coming back to Priya's question about the end. The last slide has just answered it."
DECLINE = ("Priya asks what Will earns. That is not something I should say here.\n"
           "```decline\nIt is private and not part of this lecture.\n```")


def ask_at(n, who="Priya", text="Why two sentences?"):
    """Ask once the n-th sentence of the lecture arrives."""
    count = 0

    async def on_event(ws, event):
        nonlocal count
        if event["type"] == "sentence":
            count += 1
            if count == n:
                await ws.send_json({"cmd": "ask", "who": who, "text": text})
    return on_event


def order(events):
    """The lecture as a listener lived it: slides, what was said, and what became of each question."""
    out = []
    for e in events:
        if e["type"] == "slide":
            out.append(f"slide {e['place']['index'] + 1}")
        elif e["type"] == "sentence":
            out.append("say")
        elif e["type"] == "question":
            out.append(e["question"]["state"])
        elif e["type"] in ("stopped", "floor"):
            out.append(e["type"])
    return out


async def test_a_question_is_heard_at_once_taken_at_the_break_and_answered_in_writing():
    async with Room([SLIDE_1, ANSWER, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(on_event=ask_at(1))

    # Heard straight away, while the lecturer was still on its first sentence. Taken when
    # the slide was done, and answered before the lecture moved on.
    assert order(events)[:9] == ["slide 1", "say", "heard", "say", "answering", "say", "say", "answered",
                                 "slide 2"]
    q, = listener.questions(events)
    assert (q["who"], q["text"], q["state"], q["reply"]) == ("Priya", "Why two sentences?", "answered", ANSWER)
    heard_at = q["states"][0][1]
    asked_at = [e["t"] for e in events if e["type"] == "sentence"][0]
    assert heard_at - asked_at < 0.1

    # The lecturer was told who asked, what, and the moment it was asked.
    prompt = room.host.prompts[1]
    assert prompt.startswith("Priya asks, in the chat: Why two sentences?")     # nobody stopped it
    assert 'just after you said: "Welcome to the first slide of this lecture."' in prompt
    assert 'The slides still to come are slide 2 ("Two"), slide 3 ("Three").' in prompt

    # The reply is kept with the question, and isn't mistaken for part of the slide.
    stored, = room.store.questions(room.lecture_id)
    assert stored.state == "answered" and room.store.reply(stored.id) == ANSWER
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 1, 1]
    assert room.host.prompts[2].startswith("Present slide 2 of 3")


async def test_the_standing_instructions_say_how_to_put_off_and_decline():
    async with Room([SLIDE_1, SLIDE_2, SLIDE_3]) as room:
        await room.attend()
    assert "```later" in room.host.prompts[0] and "```decline" in room.host.prompts[0]


async def test_a_question_that_is_put_off_is_come_back_to_at_the_slide_that_was_promised():
    async with Room([SLIDE_1, PUT_OFF, SLIDE_2, SLIDE_3, BACK]) as room:
        events = await room.attend(on_event=ask_at(1, text="How does it end?"))

    q, = listener.questions(events)
    assert [state for state, _ in q["states"]] == ["heard", "answering", "put_off", "answering", "answered"]
    put_off = [e["question"] for e in events if e["type"] == "question" and e["question"]["state"] == "put_off"]
    assert put_off[0]["until"] == "slide 3"
    assert put_off[0]["reply"] == "Priya asks about the end. I will come to that on the last slide."
    # It came back after the third slide was given and before the floor opened.
    flow = order(events)
    assert flow.index("slide 3") < len(flow) - 1 - flow[::-1].index("answering") < flow.index("floor")
    assert room.host.prompts[4].startswith("Earlier, Priya asked, in the chat: How does it end?")
    assert "It cannot be put off again." in room.host.prompts[4]
    assert q["reply"].endswith(BACK) and "later" not in q["reply"]


async def test_a_question_put_off_with_nowhere_to_go_is_taken_at_the_end():
    vague = "Priya asks about the end. Let me come back to that later.\n```later\nend\n```"
    again = "Coming back to Priya. Honestly, that is best left for another day.\n```later\n2\n```"
    async with Room([SLIDE_1, vague, SLIDE_2, SLIDE_3, again]) as room:
        events = await room.attend(on_event=ask_at(1))

    q, = listener.questions(events)
    assert [state for state, _ in q["states"]] == ["heard", "answering", "put_off", "answering", "answered"]
    assert [e["question"]["until"] for e in events if e["type"] == "question"][2] == "the end"
    flow = order(events)
    assert flow.index("floor") < len(flow) - 1 - flow[::-1].index("answering")
    # It can't be put off a second time. Nothing is still open when the floor is.
    assert not [x for x in room.store.questions(room.lecture_id) if x.state not in ("answered", "declined")]


async def test_a_question_can_be_declined_out_loud_with_the_reason_shown():
    async with Room([SLIDE_1, DECLINE, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(on_event=ask_at(1, text="What does Will earn?"))
    q, = listener.questions(events)
    assert q["state"] == "declined" and q["reason"] == "It is private and not part of this lecture."
    assert q["reply"] == "Priya asks what Will earns. That is not something I should say here."


async def test_questions_are_taken_in_the_order_they_were_asked():
    async def two(ws, event):
        if event["type"] == "slide" and event["place"]["index"] == 0:
            await ws.send_json({"cmd": "ask", "who": "Priya", "text": "First?"})
            await ws.send_json({"cmd": "ask", "text": "Second?"})
    async with Room([SLIDE_1, "Priya asks which came first. Hers did.",
                     "Someone asks which came second. Theirs did.", SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(on_event=two)
    first, second = listener.questions(events)
    assert (first["text"], second["text"]) == ("First?", "Second?")
    kinds = [e["question"]["id"] for e in events if e["type"] == "question" and e["question"]["state"] in ("answering", "answered")]
    assert kinds == [first["id"], first["id"], second["id"], second["id"]]  # one settled before the next is taken
    assert second["who"] == ""                                   # nobody's name is guessed
    assert room.host.prompts[2].startswith("Someone in the room asks, in the chat: Second?")


async def test_a_listener_can_stop_the_lecturer_mid_sentence_and_it_picks_the_slide_back_up():
    long_slide = ("This is the first sentence of a long slide. This is the second sentence, which gets cut off. "
                  "This third sentence is never reached at all. Nor is this fourth and final one.")
    resumed = "Picking the slide back up, here is the rest of it."
    stopped_at = None

    async def stop_then_ask(ws, event):
        nonlocal stopped_at
        if event["type"] == "sentence" and "second sentence" in event["text"]:
            await asyncio.sleep(0.05)       # partway through it
            stopped_at = event["t"] + 0.05
            await ws.send_json({"cmd": "stop"})
        if event["type"] == "stopped":
            await ws.send_json({"cmd": "ask", "who": "Priya", "text": "Wait, why?"})

    clip = b"\x00" * 1800  # 0.3 seconds, long enough to be stopped partway
    async def speak(text):
        return clip
    async with Room([long_slide, "Priya asks why. Because it helps.", resumed, SLIDE_2, SLIDE_3], speak=speak) as room:
        events = await room.attend(on_event=stop_then_ask)

    flow = order(events)
    assert flow[:10] == ["slide 1", "say", "say", "stopped", "heard", "answering", "say", "answered", "say",
                         "slide 2"]
    # The room went quiet at once: well inside the sentence that was playing.
    stopped = next(e for e in events if e["type"] == "stopped")
    assert stopped["t"] - stopped_at < 0.15
    assert next(e for e in events if e["type"] == "state" and e["t"] >= stopped["t"])["state"] == "listening"

    # The thought that was cut off was kept, and the lecturer was handed it back.
    said = room.said()
    assert ("said", "This is the second sentence, which gets cut off.", "cut off") in said
    assert ("said", "This third sentence is never reached at all. Nor is this fourth and final one.",
            "unsaid") in said
    # The lecturer's own record says it said the whole slide, so it's told what the room heard.
    assert room.host.prompts[1].startswith(
        "A listener stopped you partway through this slide. You are NOT finished with it. The last thing the "
        "room heard was: \"This is the first sentence of a long slide.\" Whatever you wrote after that was "
        "never heard.\n\nPriya asks, in the chat: Wait, why?")
    pick_up = room.host.prompts[2]
    assert "A listener stopped you there." in pick_up
    assert "do not repeat it. Pick up from where it stops:\nThis is the first sentence of a long slide." in pick_up
    assert pick_up.rstrip().endswith("This is the second sentence, which gets cut off. This third sentence is "
                                     "never reached at all. Nor is this fourth and final one.")
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 1, 1]


async def test_a_stopped_lecturer_waits_until_it_is_told_to_carry_on():
    waited = None

    async def stop_then_carry_on(ws, event):
        nonlocal waited
        if event["type"] == "sentence" and waited is None:
            waited = False
            await ws.send_json({"cmd": "stop"})
        if event["type"] == "state" and event["message"].startswith("Stopped."):
            await asyncio.sleep(0.5)
            waited = True
            await ws.send_json({"cmd": "carry_on"})

    async with Room([SLIDE_1, "Carrying on with the first slide now.", SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(on_event=stop_then_carry_on)
    held = next(e for e in events if e["type"] == "state" and e["message"].startswith("Stopped."))
    after = [e for e in events if e["t"] > held["t"] and e["type"] == "sentence"]
    assert waited and after[0]["t"] - held["t"] >= 0.5          # nothing was said while it was held
    assert held["message"] == "Stopped. Ask a question, or press Carry on."
    assert [t["slide"] for t in listener.slide_turns(events)] == [0, 1, 2]


async def test_a_question_asked_while_the_lecturer_cannot_be_reached_waits_and_is_not_shown_as_heard():
    host = FakeHost([SLIDE_1, SLIDE_2, "Priya asks if I am there. I am now.", SLIDE_3], chars_per_s=4000,
                    failures={1: 0})

    async def ask_when_stuck(ws, event):
        if event["type"] == "state" and event["state"] == "stuck":
            await ws.send_json({"cmd": "ask", "who": "Priya", "text": "Are you there?"})

    async with Room(None, host=host, retry_delays_s=(0.3,)) as room:
        events = await room.attend(on_event=ask_when_stuck)
    q, = listener.questions(events)
    assert [state for state, _ in q["states"]] == ["waiting", "heard", "answering", "answered"]


async def test_a_question_at_the_end_of_the_main_line_is_taken_straight_away():
    asked = False

    async def ask_at_the_floor(ws, event):
        nonlocal asked
        if event["type"] == "state" and event["message"].startswith("End of the main line") and not asked:
            asked = True
            await ws.send_json({"cmd": "ask", "who": "Priya", "text": "One more thing?"})
            return None
        if event["type"] == "question" and event["question"]["state"] == "answered":
            return "leave"
        return "stay" if asked else None

    async with Room([SLIDE_1, SLIDE_2, SLIDE_3, "Priya has one more thing. Here it is, briefly.\n```later\n1\n```"]) as room:
        # Sit past the first "floor is open": this listener leaves when the question is answered.
        record = await listener.attend(room.url, timeout_s=20, on_event=ask_at_the_floor, stay=True)
        events = record["events"]
        assert "It cannot be put off" in room.host.prompts[3]
    q, = listener.questions(events)
    assert [state for state, _ in q["states"]] == ["heard", "answering", "answered"]


async def test_ending_a_lecture_leaves_no_question_hanging():
    async def ask_then_end(ws, event):
        if event["type"] == "sentence":
            await ws.send_json({"cmd": "ask", "who": "Priya", "text": "Will you get to this?"})
            await ws.send_json({"cmd": "end"})
    async with Room([SLIDE_1, SLIDE_2, SLIDE_3]) as room:
        events = await room.attend(on_event=ask_then_end)
    q, = listener.questions(events)
    assert q["state"] == "declined" and q["reason"] == "The lecture ended before this was taken."


async def test_someone_who_arrives_late_sees_every_question_and_what_became_of_it():
    async with Room([SLIDE_1, ANSWER, SLIDE_2, SLIDE_3]) as room:
        await room.attend(on_event=ask_at(1))
        async with aiohttp.ClientSession() as http, http.ws_connect(room.url) as ws:
            hello = json.loads((await ws.receive()).data)
            # Nothing is taken once a lecture has ended, and the asker is told.
            await room.lecture.end()
            await ws.send_json({"cmd": "ask", "text": "Too late?"})
            replies = [json.loads((await ws.receive()).data) for _ in range(2)]
    assert [(q["who"], q["state"], q["reply"]) for q in hello["questions"]] == [("Priya", "answered", ANSWER)]
    assert replies[-1] == {"type": "warn", "message": "The lecture has ended, so that wasn't taken."}
    assert len(room.store.questions(room.lecture_id)) == 1
