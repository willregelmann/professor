import time

import pytest

from professor.hosts.fake import FakeHost


async def collect(host, prompt):
    return [piece async for piece in host.turn(prompt)]


async def test_replays_each_reply_in_turn_and_keeps_the_prompts():
    host = FakeHost(["first reply", "second reply"], chars_per_s=10_000)
    assert "".join(await collect(host, "one")) == "first reply"
    assert "".join(await collect(host, "two")) == "second reply"
    assert host.prompts == ["one", "two"]
    assert host.session


async def test_text_arrives_in_pieces_at_the_set_pace():
    host = FakeHost(["x" * 100], chars_per_s=500, chunk=10)
    t0 = time.monotonic()
    pieces = await collect(host, "go")
    assert len(pieces) == 10
    assert 0.15 < time.monotonic() - t0 < 0.6


async def test_cancel_stops_the_turn_and_keeps_what_was_said():
    host = FakeHost(["x" * 1000], chars_per_s=1000, chunk=10)
    got = []
    async for piece in host.turn("go"):
        got.append(piece)
        if len(got) == 3:
            await host.cancel()
    assert len(got) == 3


async def test_running_out_of_script_is_an_error_not_silence():
    host = FakeHost(["only one"], chars_per_s=10_000)
    await collect(host, "one")
    with pytest.raises(RuntimeError, match="no reply scripted"):
        await collect(host, "two")


async def test_the_notes_reader_reads_the_notes_and_owns_up_when_asked_something():
    from professor.hosts.fake import NotesReader
    host = NotesReader(chars_per_s=10_000)
    slide = ("Present slide 1 of 3, \"One\". Speaker notes (cover these in your own words, about 35 seconds "
             "of speech, then stop):\nThe notes, as written.\n\nThis diagram is ALREADY on screen")
    assert "".join(await collect(host, slide)) == "The notes, as written."
    assert "can't answer that" in "".join(await collect(host, "Priya asks, in the chat: Why?"))
