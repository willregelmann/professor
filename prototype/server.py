#!/usr/bin/env python3
"""Wren presenter POC: a deck narrated live, in a live voice, by a fresh Hermes session.

Browser <-ws-> this server -> gateway api_server (/v1/chat/completions, stream)
                            -> edge-tts per sentence -> mp3 bytes back over the ws.

Nothing is pre-recorded. Each slide's narration is generated from its notes at the
moment it is presented; each question is answered live in the same session, so the
talk has memory of what was already said. Speech is cut sentence by sentence as
tokens arrive, so the first sentence plays while the rest is still being written.

Run:  ~/.hermes/hermes-agent/venv/bin/python ~/presenter/server.py
Open: http://ha-pi.local:8770
"""
import asyncio, json, os, re, time, uuid
from pathlib import Path

import aiohttp
from aiohttp import web
import edge_tts

HERE = Path(__file__).resolve().parent
API = "http://127.0.0.1:8642/v1/chat/completions"
VOICE = os.environ.get("PRESENTER_VOICE", "en-US-AriaNeural")
PORT = int(os.environ.get("PRESENTER_PORT", "8770"))
SENTENCE_END = re.compile(r"[.!?](?=\s)")
FENCE_OPEN = "```mermaid"
FENCE = "```"


def _api_key() -> str:
    # Read at runtime from the gateway's own .env; never logged, never sent to the browser.
    for line in (Path.home() / ".hermes" / ".env").read_text().splitlines():
        if line.startswith("API_SERVER_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("API_SERVER_KEY not found in ~/.hermes/.env")


DECK = json.loads((HERE / "deck.json").read_text())
PREAMBLE = (
    "You are Wren, presenting live, out loud, to a room of software engineers. "
    "Everything you write is converted to speech the moment you write it, so: plain spoken "
    "prose only, no markdown, no lists, no headings, no emoji. Short sentences. "
    "Speak in the first person as yourself. Do not use tools unless a question genuinely needs "
    "a live check.\n\n"
    "VISUAL AIDS. You can put a diagram on the screen behind you. Write a Mermaid diagram inside "
    "a block that starts with a line ```mermaid and ends with a line ```. That block is DRAWN, "
    "never spoken, and appears on screen at the moment your speech reaches it, so introduce it "
    "in speech first (for example: \"here is how that flows\") and then talk the room through it. "
    "Use one only when a picture genuinely helps a technical question: flows between components, "
    "sequences of messages, layers, state machines. At most one per answer. Keep it small, about "
    "twelve nodes or fewer, and prefer flowchart LR, flowchart TD, or sequenceDiagram. In a "
    "flowchart, put every node label containing spaces or punctuation in double quotes, e.g. "
    "A[\"api_server (port 8642)\"]. In a sequenceDiagram use NO quotes at all -- participant aliases "
    "and message text are literal there, so quotes would appear on screen. "
    "No styling, no classDef, no click handlers.\n\n"
    "GROUNDING FACTS -- stay inside these when describing how you and Ash talk, and say plainly "
    "when a question goes beyond them rather than inventing detail. You run on a Raspberry Pi "
    "in the house; Ash runs on Will's workstation. Each of you is a separate Hermes gateway. "
    "When you hand Ash something, a tool called tell_partner makes an HTTP POST to Ash's gateway, "
    "and that starts a turn in your conversation thread on his side; he answers the same way, "
    "into a turn on yours. There is no shared memory and no message bus in between. A 200 from "
    "the POST only means the request was accepted, so neither of you treats it as delivery: "
    "delivery is confirmed only by its effect, for example Ash's reply arriving or the change he "
    "made being visible.\n\n"
    "Here is the whole deck so you know where the talk is going:\n\n"
    + "\n".join(f"Slide {i+1}: {s['title']}" + (" (has a diagram on screen)" if s.get("diagram") else "")
                 for i, s in enumerate(DECK["slides"]))
)


class Talk:
    """One presentation = one Hermes session, continued across slides and questions."""

    def __init__(self, key: str):
        self.key = key
        self.session_id = ""
        self.started = False

    async def stream_text(self, http: aiohttp.ClientSession, prompt: str):
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if self.session_id:
            headers["X-Hermes-Session-Id"] = self.session_id
        if not self.started:
            prompt = PREAMBLE + "\n\n" + prompt
            self.started = True
        body = {"model": "hermes", "stream": True, "messages": [{"role": "user", "content": prompt}]}
        async with http.post(API, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=300)) as r:
            if r.status != 200:
                raise RuntimeError(f"api_server {r.status}: {(await r.text())[:300]}")
            self.session_id = r.headers.get("X-Hermes-Session-Id", self.session_id)
            async for raw in r.content:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


def _speakable(text: str) -> str:
    """Never read markup aloud: a stray fence or backtick is noise to a listener."""
    return text.replace("`", "").strip()


async def speak(text: str) -> bytes:
    out = bytearray()
    async for ch in edge_tts.Communicate(text, VOICE).stream():
        if ch["type"] == "audio":
            out += ch["data"]
    return bytes(out)


async def narrate(ws, talk: Talk, http, prompt: str, tag: dict):
    """Stream the model's text, cut it into sentences, speak each as soon as it exists."""
    t0 = time.monotonic()
    buf, first_audio, n = "", None, 0
    tts_queue: asyncio.Queue = asyncio.Queue()

    async def speaker():
        nonlocal first_audio, n
        while (sentence := await tts_queue.get()) is not None:
            if isinstance(sentence, dict):  # a diagram: sent IN ORDER with the speech around it
                await ws.send_json({"type": "diagram", **sentence, **tag})
                continue
            audio = await speak(sentence)
            # TTS CAN FAIL BY RETURNING NOTHING INSTEAD OF RAISING. edge_tts yielding
            # zero audio chunks gives b"" with no exception, and an empty audio/mpeg
            # Blob is INERT in the browser: no onerror, no oncanplay, no onended,
            # readyState stays 0. The client would set playing=true and wait forever
            # on an event that never comes -- the queue deadlocks and the status line
            # still prints success stats over a dead player. Measured in real Chrome
            # over CDP (Ash, 2026-09-24), including a truncated 6-byte mp3: same.
            # So refuse to send the empty frame and say so where the room can see it.
            if not audio:
                # "warn", not "error": the turn CONTINUES after a dead sentence, so the page
                # must not treat this as the end of the turn (clearing busy here let a queued
                # question start a second, overlapping answer). And the text rides under
                # "message" -- the page reads m.message; the first version sent "error" and
                # the room would have seen "ERROR: undefined".
                await ws.send_json({
                    "type": "warn",
                    "message": f"TTS returned no audio for: {sentence[:80]!r}",
                    **tag,
                })
                continue
            if first_audio is None:
                first_audio = time.monotonic() - t0
            n += 1
            await ws.send_json({"type": "sentence", "text": sentence, **tag})
            await ws.send_bytes(audio)

    async def cut_sentences(text: str, flush: bool) -> str:
        # Cut at sentence ends as tokens arrive. A sentence under 20 chars ("Hello.") is
        # MERGED with the next rather than blocking: the first version stopped scanning at
        # a short opener, so a whole reply queued behind "Hello." and spoke only at the end.
        start = 0
        for m in SENTENCE_END.finditer(text):
            if len(text[start:m.end()].strip()) >= 20:
                await tts_queue.put(_speakable(text[start:m.end()]))
                start = m.end()
        rest = text[start:]
        if flush and rest.strip():
            await tts_queue.put(_speakable(rest))
            rest = ""
        return rest

    task = asyncio.create_task(speaker())
    in_diagram, diagrams = False, 0
    async for delta in talk.stream_text(http, prompt):
        buf += delta
        while True:
            if not in_diagram:
                i = buf.find(FENCE_OPEN)
                if i < 0:
                    # Hold back a trailing partial fence ("``", "```merm") so it is never spoken.
                    tail = buf.rfind("`")
                    safe = buf if tail < 0 or len(buf) - tail > len(FENCE_OPEN) else buf[:tail]
                    buf = await cut_sentences(safe, flush=False) + buf[len(safe):]
                    break
                await cut_sentences(buf[:i], flush=True)  # speech before the picture goes first
                buf, in_diagram = buf[i + len(FENCE_OPEN):], True
            else:
                j = buf.find(FENCE)
                if j < 0:
                    break
                code = buf[:j].strip()
                if code:
                    diagrams += 1
                    await tts_queue.put({"code": code})
                buf, in_diagram = buf[j + len(FENCE):], False
    if in_diagram and buf.strip():  # unterminated block: still draw it rather than speak it
        diagrams += 1
        await tts_queue.put({"code": buf.strip()})
    elif buf.strip():
        await cut_sentences(buf, flush=True)
    await tts_queue.put(None)
    await task
    stats = {"first_audio_s": round(first_audio or -1, 2), "total_s": round(time.monotonic() - t0, 2),
             "sentences": n, "diagrams": diagrams}
    print(json.dumps({**tag, **stats}), flush=True)
    await ws.send_json({"type": "done", **tag, **stats})


async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    talk = Talk(request.app["key"])
    await ws.send_json({"type": "deck", "slides": [{"title": s["title"], "diagram": s.get("diagram", "")}
                                                   for s in DECK["slides"]]})
    async with aiohttp.ClientSession() as http:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            cmd = json.loads(msg.data)
            try:
                if cmd.get("cmd") == "slide":
                    i = int(cmd["n"])
                    s = DECK["slides"][i]
                    prompt = (f"Present slide {i+1} of {len(DECK['slides'])}, \"{s['title']}\". "
                              f"Speaker notes (cover these in your own words, about {s.get('seconds', 45)} "
                              f"seconds of speech, then stop):\n{s['notes']}")
                    if s.get("diagram"):
                        prompt += ("\n\nThis diagram is ALREADY on screen for this slide; walk the room "
                                   f"through it and do not draw another:\n{s['diagram']}")
                    await narrate(ws, talk, http, prompt, {"slide": i})
                elif cmd.get("cmd") == "question":
                    who = (cmd.get("who") or "someone in the audience").strip()[:60]
                    q = (cmd.get("text") or "").strip()[:1000]
                    prompt = (f"[{who}] asks, in the meeting chat: {q}\n\nAnswer out loud, briefly "
                              "(under 30 seconds of speech), then stop.")
                    await narrate(ws, talk, http, prompt, {"question": q, "who": who})
            except Exception as exc:  # surface failures on the page, not only in a log
                await ws.send_json({"type": "error", "message": str(exc)[:400]})
    return ws


async def index(request):
    return web.FileResponse(HERE / "index.html")


def main():
    app = web.Application()
    app["key"] = _api_key()
    app.router.add_get("/", index)
    app.router.add_static("/static/", HERE / "static")
    app.router.add_get("/ws", ws_handler)
    web.run_app(app, host="0.0.0.0", port=PORT, print=lambda *_: print(f"presenter on :{PORT}", flush=True))


if __name__ == "__main__":
    main()
