"""The harness itself: the playback arithmetic, the spike's delivery loop, and the
headless listener driven against the unchanged prototype with a fake gateway."""
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bench import listener, metrics, spikes
from professor.hosts.fake import FakeHost

PROTOTYPE = Path(__file__).resolve().parent.parent / "prototype"
CLIP = b"\x00" * 6000  # one second of 48 kbit/s audio


def test_playback_counts_only_the_waiting_after_the_first_word():
    # 1s clips arriving at 2s, 2.5s (queued behind the first) and 6s (2s late).
    m = metrics.playback([(2.0, 1.0), (2.5, 1.0), (6.0, 1.0)])
    assert m == {"first_audio_s": 2.0, "audio_s": 3.0, "dead_air_s": 2.0, "longest_gap_s": 2.0, "stalls": 1}


def test_playback_of_nothing():
    assert metrics.playback([])["first_audio_s"] is None


async def test_deliver_keeps_speech_and_drawings_in_order(monkeypatch):
    async def speak(text, voice=None):
        return CLIP
    monkeypatch.setattr(spikes.speech, "speak", speak)
    host = FakeHost(spikes.FAKE_REPLIES, chars_per_s=5000)
    await spikes.deliver(host, "slide")
    t = await spikes.deliver(host, "question")
    assert [kind for kind, _ in t["seq"]] == ["SAY", "DRAW", "SAY"]
    assert t["first_text_s"] is not None and t["first_audio_s"] is not None
    assert t["audio_s"] == 2.0


async def test_deliver_reports_a_sentence_that_could_not_be_spoken(monkeypatch):
    async def speak(text, voice=None):
        return b"" if "scripted" in text else CLIP
    monkeypatch.setattr(spikes.speech, "speak", speak)
    t = await spikes.deliver(FakeHost(spikes.FAKE_REPLIES, chars_per_s=5000), "slide")
    assert t["warnings"] == 1 and t["sentences"] == 1


def test_the_spike_prompts_are_the_prototypes_own():
    turns = dict(spikes.prompts())
    assert turns["slide"].startswith("You are Wren, presenting live")
    assert "Present slide 1 of 3" in turns["slide"]
    assert "ALREADY on screen" in turns["slide-later"]


@pytest.fixture
async def prototype(monkeypatch):
    """The prototype's server, unchanged, with a scripted gateway and a silent
    speech engine standing in for Hermes and edge-tts."""
    host = FakeHost(spikes.FAKE_REPLIES, chars_per_s=5000)

    async def completions(request):
        body = await request.json()
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                           "X-Hermes-Session-Id": "fake-session"})
        await resp.prepare(request)
        async for piece in host.turn(body["messages"][0]["content"]):
            await resp.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    gateway = web.Application()
    gateway.router.add_post("/v1/chat/completions", completions)
    gw = TestServer(gateway)
    await gw.start_server()

    spec = importlib.util.spec_from_file_location("prototype_server_under_test", PROTOTYPE / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    async def speak(text):
        return b"" if "cut as it arrives" in text else CLIP
    monkeypatch.setattr(mod, "API", str(gw.make_url("/v1/chat/completions")))
    monkeypatch.setattr(mod, "speak", speak)

    app = web.Application()
    app["key"] = "test"
    app.router.add_get("/ws", mod.ws_handler)
    srv = TestServer(app)
    await srv.start_server()
    yield str(srv.make_url("/ws")), host
    await srv.close()
    await gw.close()


async def test_listener_records_a_session_against_the_prototype(prototype):
    url, host = prototype
    turns = dict(await listener.session(url))

    # Turn 1: the first reply's second sentence gets no audio, and the turn carries on.
    slide = turns["slide"]
    assert slide["errors"] == 0 and slide["warnings"] == 1
    assert slide["sentences"] == 1 and slide["first_audio_s"] is not None
    assert host.prompts[0].startswith("You are Wren")       # the first turn carries the preamble
    assert not host.prompts[1].startswith("You are Wren")   # later turns don't

    # Turn 2: the drawing lands between the two sentences it was written between.
    q = turns["question"]
    assert [kind for kind, _ in q["seq"]] == ["SAY", "DRAW", "SAY"]
    assert q["server"]["diagrams"] == 1 and q["audio_s"] == 2.0

    assert turns["slide-later"]["sentences"] == 2
