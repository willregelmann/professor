"""Stage 5: one core, many adapters. The same lecture, given through each host,
must leave the same record. A behaviour that differs by host is a bug."""
import asyncio
import json
import re
import threading
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bench import listener
from professor import mcp_server
from professor.hosts.claude_sdk import ClaudeHost
from professor.hosts.fake import FakeHost
from professor.hosts.hermes import HermesHost
from professor.hosts.tools_only import NOTHING_YET, STOPPED, ToolsOnlyHost
from professor.showcheck import Check
from tests.test_lecture import Room

# One lecture, as the lecturer writes it: three slides, a question answered with a
# detour and a slide made up on the spot, one put off and come back to, one declined.
SCRIPT = [
    "Welcome to the first slide of this lecture. It has two sentences to say.",
    "Priya asks how the pieces fit. Let me put a sketch of them on the screen.\n"
    "```slide\ntitle: The pieces\ndiagram:\nflowchart LR\n  A --> B\n```\n"
    "One piece leads to the next. Back to where we were.\n```go\nreturn\n```",
    "Marcus asks how it ends. That is what the last slide covers, so I will come to it there.\n```later\n3\n```",
    "This is the second slide, and it is short. It is here to sit in the middle.",
    "Someone asks for my password. That is not something I will share here.\n```decline\nIt is private.\n```",
    "This is the last slide of the deck. After it the floor is open.",
    "Coming back to Marcus's question about the end. The last slide has just answered it.",
]


def checked(diagram="", math=""):
    return Check(checked=True)


class Asker:
    """Asks the same three things at the same points, whoever the lecturer is."""

    def __init__(self):
        self.slide, self.asked = -1, set()

    async def __call__(self, ws, event):
        if event["type"] == "slide" and not event["place"]["detour"]:
            self.slide = event["place"]["index"]
        if event["type"] == "sentence" and self.slide == 0 and 0 not in self.asked:
            self.asked.add(0)
            await ws.send_json({"cmd": "ask", "who": "Priya", "text": "How do the pieces fit?"})
            await ws.send_json({"cmd": "ask", "who": "Marcus", "text": "How does it end?"})
        if event["type"] == "sentence" and self.slide == 1 and 1 not in self.asked:
            self.asked.add(1)
            await ws.send_json({"cmd": "ask", "text": "What is your password?"})


def record(room, events):
    """What the lecture kept, and what a listener saw become of each question."""
    store, lecture = room.store, room.lecture_id
    return {
        "path": [(v["slide_id"], v["how"], v["covered"]) for v in store.path(lecture)],
        "said": [(u.kind, u.text, u.failed, u.question_id) for u in store.utterances(lecture)],
        "questions": [(q.asker, q.text, q.state, q.until_slide_id, q.reason, store.reply(q.id))
                      for q in store.questions(lecture)],
        "seen": [[state for state, _ in q["states"]] for q in listener.questions(events)],
        "made_up": [(s.title, s.diagram, s.anchor_slide_id, s.question_id, s.show_checked)
                    for s in store.beside(room.deck.id)],
        "main_line": [s.id for s in store.main_line(room.deck.id)],
    }


async def through_fake():
    async with Room(SCRIPT, check=checked) as room:
        return record(room, await room.attend(on_event=Asker()))


async def through_hermes():
    replies = iter(SCRIPT)

    async def completions(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "X-Hermes-Session-Id": "s"})
        await resp.prepare(request)
        reply = next(replies)
        for i in range(0, len(reply), 7):
            await resp.write(f"data: {json.dumps({'choices': [{'delta': {'content': reply[i:i + 7]}}]})}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    gateway = web.Application()
    gateway.router.add_post("/v1/chat/completions", completions)
    server = TestServer(gateway)
    await server.start_server()
    host = HermesHost(str(server.make_url("")), "key")
    try:
        async with Room(None, host=host, check=checked) as room:
            return record(room, await room.attend(on_event=Asker()))
    finally:
        await host.close()
        await server.close()


class StubClaude:
    """Stands in for the SDK's client: streams each scripted reply as partial messages."""

    def __init__(self, replies):
        self.replies, self.interrupts, self.queries = list(replies), 0, []
        self._pending = []

    async def connect(self): pass
    async def disconnect(self): pass
    async def interrupt(self): self.interrupts += 1

    async def query(self, prompt):
        self.queries.append(prompt)
        reply = self.replies.pop(0)
        self._pending = [SimpleNamespace(event={"type": "content_block_delta",
                                                "delta": {"type": "text_delta", "text": reply[i:i + 9]}})
                         for i in range(0, len(reply), 9)]
        self._pending.insert(0, SimpleNamespace(event={"type": "content_block_delta",
                                                       "delta": {"type": "thinking_delta", "thinking": "hm"}}))
        self._pending.append(SimpleNamespace(session_id="claude-session", is_error=False))

    async def receive_response(self):
        while self._pending:
            msg = self._pending.pop(0)
            await asyncio.sleep(0)
            yield msg
            if hasattr(msg, "session_id"):
                return


async def through_claude():
    async with Room(None, host=ClaudeHost(client=lambda: StubClaude(SCRIPT)), check=checked) as room:
        return record(room, await room.attend(on_event=Asker()))


async def scripted_agent(url, replies):
    """An agent with tools and nothing else: it listens, says its piece a passage at a time, and listens again."""
    base = url.replace("/ws", "")
    replies = list(replies)
    async with aiohttp.ClientSession() as http:
        while True:
            async with http.post(f"{base}/host/listen") as r:
                text = (await r.json())["text"]
            if text == NOTHING_YET or not replies:
                await asyncio.sleep(0.01)
                continue
            # One passage per paragraph, with any block kept whole inside its passage.
            for passage in re.split(r"\n(?=[A-Z])", replies.pop(0)):
                async with http.post(f"{base}/host/say", json={"text": passage}) as r:
                    assert (await r.json())["text"] == "Heard."


async def through_tools_only():
    async with Room(None, host=ToolsOnlyHost(listen_s=0.3), check=checked) as room:
        agent = asyncio.create_task(scripted_agent(room.url, SCRIPT))
        try:
            return record(room, await room.attend(on_event=Asker()))
        finally:
            agent.cancel()


async def test_the_same_lecture_leaves_the_same_record_through_every_host():
    reference = await through_fake()
    # The script did what it was written to do.
    assert [state for *_, state, _until, _reason, _reply in reference["questions"]] == [
        "answered", "answered", "declined"]
    assert reference["seen"][1] == ["heard", "answering", "put_off", "answering", "answered"]
    assert [how for _, how, _ in reference["path"]] == ["start", "made up", "return", "next", "next"]
    assert len(reference["made_up"]) == 1 and len(reference["main_line"]) == 3

    for name, through in (("hermes", through_hermes), ("claude", through_claude), ("tools", through_tools_only)):
        got = await through()
        for part in reference:
            assert got[part] == reference[part], f"{name}: {part} differs"


async def test_with_no_agent_listening_the_lecture_says_so_and_goes_on_when_one_arrives():
    async with Room(None, host=ToolsOnlyHost(wait_for_agent_s=0.2, listen_s=0.3), check=checked,
                    retry_delays_s=(0.2, 0.2, 0.2, 0.2)) as room:
        agent = None

        async def start_the_agent_once_stuck(ws, event):
            nonlocal agent
            if event["type"] == "state" and event["state"] == "stuck" and agent is None:
                assert "no agent is listening for this lecture" in event["message"]
                agent = asyncio.create_task(scripted_agent(room.url, SCRIPT[:1] + SCRIPT[3:4] + SCRIPT[5:6]))
        events = await room.attend(on_event=start_the_agent_once_stuck)
        agent.cancel()
    assert [t["slide"] for t in listener.slide_turns(events)] == [0, 1, 2]
    assert [v["covered"] for v in room.store.path(room.lecture_id)] == [1, 1, 1]


async def test_a_stopped_tools_only_lecturer_is_told_its_words_were_not_heard():
    host = ToolsOnlyHost(listen_s=5)
    listening = asyncio.create_task(host.listen())
    turn = host.turn("Present slide 1")
    first = asyncio.create_task(turn.__anext__())
    assert (await listening).startswith("Present slide 1") and "HOW YOU ARE HEARD" in await listening
    assert await host.say("One passage.") == "Heard."
    assert await first == "One passage.\n"
    await host.cancel()
    await turn.aclose()
    assert await host.say("Another passage.") == STOPPED
    # It calls listen, and the next turn reaches it with only a short reminder.
    listening = asyncio.create_task(host.listen())
    turn = host.turn("Priya asks why.")
    first = asyncio.create_task(turn.__anext__())
    assert await listening == "Priya asks why.\n\nSpeak through say, a passage at a time, then call listen."
    assert await host.say("Because.") == "Heard."
    assert await first == "Because.\n"
    again = asyncio.create_task(host.listen())      # calling listen ends the turn
    with pytest.raises(StopAsyncIteration):
        await turn.__anext__()
    again.cancel()


async def test_the_lecturers_two_tools_exist_only_for_a_lecture_that_speaks_through_tools(monkeypatch):
    async with Room(["One reply."], check=checked) as room:       # a fake host, not a tools-only one
        async with aiohttp.ClientSession() as http, http.post(room.url.replace("/ws", "/host/listen")) as r:
            assert r.status == 404
    plain = mcp_server.Server(":memory:").handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    lecturer = mcp_server.Server(":memory:", lecturer=True).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = lambda reply: [t["name"] for t in reply["result"]["tools"]]
    assert "say" not in names(plain) and "listen" not in names(plain)
    assert names(lecturer)[-2:] == ["listen", "say"] and "correct" in names(plain)


async def test_say_and_listen_reach_the_lecture_through_the_tool_server(monkeypatch):
    host = ToolsOnlyHost(listen_s=0.3)
    async with Room(None, host=host, check=checked) as room:
        monkeypatch.setenv("PROFESSOR_URL", room.url.replace("/ws", ""))
        server = mcp_server.Server(":memory:", lecturer=True)

        def call(name, **args):
            reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": name, "arguments": args}})
            return reply["result"]["content"][0]["text"], reply["result"]["isError"]

        # The tool server is a blocking program of its own, so it runs off the event loop here.
        assert await asyncio.to_thread(call, "listen") == (NOTHING_YET, False)
        turn = host.turn("Present slide 1")
        first = asyncio.create_task(turn.__anext__())
        prompt, failed = await asyncio.to_thread(call, "listen")
        assert prompt.startswith("Present slide 1") and not failed
        assert await asyncio.to_thread(call, "say", text="Through the tool server.") == ("Heard.", False)
        assert await first == "Through the tool server.\n"
        await turn.aclose()

    monkeypatch.setenv("PROFESSOR_URL", "http://127.0.0.1:9")
    text, failed = await asyncio.to_thread(call, "listen")
    assert failed and "No lecture could be reached at http://127.0.0.1:9" in text and "--host tools" in text


async def test_a_claude_turn_that_was_cut_short_is_read_past_before_the_next_one():
    stub = StubClaude(["First reply, which gets interrupted partway through.", "Second reply."])
    host = ClaudeHost(client=lambda: stub)
    turn = host.turn("one")
    assert await turn.__anext__()
    await host.cancel()
    await turn.aclose()
    assert stub.interrupts == 1
    assert "".join([d async for d in host.turn("two")]) == "Second reply."    # none of the first reply's tail
    assert host.session == "claude-session"
    await host.cancel()                                                       # nothing in progress: nothing to interrupt
    assert stub.interrupts == 1
    await host.close()


async def test_a_claude_turn_that_ends_in_an_error_is_a_failure_and_no_sdk_is_said_plainly(monkeypatch):
    class Failing(StubClaude):
        async def query(self, prompt):
            self._pending = [SimpleNamespace(session_id="s", is_error=True, subtype="error_during_execution")]
    with pytest.raises(RuntimeError, match="ended the turn with an error"):
        [d async for d in ClaudeHost(client=lambda: Failing([])).turn("one")]

    import builtins
    real_import = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name == "claude_agent_sdk":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_sdk)
    with pytest.raises(RuntimeError, match=r"isn't installed \(pip install 'professor\[claude\]'\)"):
        [d async for d in ClaudeHost().turn("one")]
