"""A headless listener: plays the page's role over the websocket and records what
a listener would have experienced.

It speaks two protocols. Against the prototype (prototype/index.html) it drives
each turn itself, as the prototype's page does:

    python -m bench.listener --url http://ha-pi.local:8770/ws --host hermes --runs 5

Each run is a fresh connection, which is a fresh host session: a slide turn that
carries the standing instructions, a question, then a second slide.

Against Professor's own server it presses Start and sits through the lecture,
which paces itself:

    python -m bench.listener --attend --url http://localhost:8770/ws --host fake
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import aiohttp

from bench import log, metrics
from professor.lecture import FLOOR

QUESTION = {"who": "Marcus",
            "text": "How does a message actually get from you to Ash and back? Walk me through the hops."}
SCRIPT = [("slide", {"cmd": "slide", "n": 0}),
          ("question", {"cmd": "question", **QUESTION}),
          ("slide-later", {"cmd": "slide", "n": 1})]


async def turn(ws: aiohttp.ClientWebSocketResponse, cmd: dict, *, timeout_s: float = 300) -> dict:
    """Send one command and record everything that comes back until the turn ends."""
    t0 = time.monotonic()
    seq: list[list] = []          # ["SAY", text] | ["DRAW", code] | ["WARN", msg] | ["ERROR", msg]
    clips: list[tuple[float, float]] = []
    server: dict = {}
    await ws.send_json(cmd)
    async with asyncio.timeout(timeout_s):
        async for m in ws:
            at = time.monotonic() - t0
            if m.type == aiohttp.WSMsgType.BINARY:
                clips.append((at, metrics.mp3_duration_s(len(m.data))))
                continue
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            d = json.loads(m.data)
            if d["type"] == "sentence":
                seq.append(["SAY", d["text"]])
            elif d["type"] == "diagram":
                seq.append(["DRAW", d["code"]])
            elif d["type"] == "warn":
                seq.append(["WARN", d.get("message")])
            elif d["type"] == "error":
                seq.append(["ERROR", d.get("message") or d.get("error")])
                break
            elif d["type"] == "done":
                server = {k: d.get(k) for k in ("first_audio_s", "total_s", "sentences", "diagrams")}
                break
        else:
            seq.append(["ERROR", "connection closed before the turn ended"])
    return {
        "cmd": cmd,
        "total_s": round(time.monotonic() - t0, 2),
        **metrics.playback(clips),
        "sentences": sum(1 for kind, _ in seq if kind == "SAY"),
        "diagrams": sum(1 for kind, _ in seq if kind == "DRAW"),
        "warnings": sum(1 for kind, _ in seq if kind == "WARN"),
        "errors": sum(1 for kind, _ in seq if kind == "ERROR"),
        "server": server,
        "seq": seq,
    }


async def session(url: str, script=SCRIPT) -> list[tuple[str, dict]]:
    """One connection, one host session: run the script's turns in order."""
    out = []
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, max_msg_size=0) as ws:
            json.loads((await ws.receive()).data)  # the deck
            for name, cmd in script:
                out.append((name, await turn(ws, cmd)))
    return out


async def attend(url: str, *, draw_fails=None, on_event=None, start: bool = True, stay: bool = False,
                 timeout_s: float = 900) -> dict:
    """Sit through a lecture on Professor's server, from Start to the end of the
    main line, and record everything a listener was sent and when.

    `draw_fails(code)` stands in for the page's renderer: it returns an error for
    a diagram that wouldn't draw, and the listener reports it as the page would.
    `on_event(ws, event)` lets a test act as a listener would, mid-lecture, and
    leave when it returns "leave".
    """
    events: list[dict] = []
    t0 = time.monotonic()
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, max_msg_size=0) as ws:
            hello = json.loads((await ws.receive()).data)
            if start:
                await ws.send_json({"cmd": "start"})
            async with asyncio.timeout(timeout_s):
                async for m in ws:
                    if m.type != aiohttp.WSMsgType.TEXT:
                        continue
                    d = json.loads(m.data)
                    d["t"] = round(time.monotonic() - t0, 3)
                    if "audio" in d:
                        d["bytes"] = len(d.pop("audio")) * 3 // 4
                    events.append(d)
                    if draw_fails and d["type"] == "diagram" and (error := draw_fails(d["code"])):
                        await ws.send_json({"cmd": "draw_failed", "id": d["id"], "error": error})
                    if (draw_fails and d["type"] == "slide" and d["slide"]["diagram"]
                            and (error := draw_fails(d["slide"]["diagram"]))):
                        await ws.send_json({"cmd": "slide_failed", "revision": d["slide"]["revision"],
                                            "error": error})
                    if on_event and await on_event(ws, d) == "leave":
                        break
                    # The lecture is over for this listener when it ends, or when the
                    # floor is open and there is nothing left for the lecturer to take.
                    # With `stay`, only `on_event` (or the lecture ending) decides when to go.
                    if d["type"] == "state" and (d["state"] == "ended" or (d.get("message") == FLOOR and not stay)):
                        break
    return {"hello": hello, "events": events}


def questions(events: list[dict]) -> list[dict]:
    """Each question a listener saw, with every state it went through and when."""
    seen: dict[int, dict] = {}
    for e in events:
        if e["type"] != "question":
            continue
        q = e["question"]
        mine = seen.setdefault(q["id"], {"id": q["id"], "who": q["who"], "text": q["text"], "states": []})
        mine["states"].append((q["state"], e["t"]))
        mine.update(state=q["state"], until=q["until"], reason=q["reason"], reply=q["reply"])
    return list(seen.values())


def slide_turns(events: list[dict]) -> list[dict]:
    """What each slide was like to sit through."""
    turns = []
    starts = [i for i, e in enumerate(events) if e["type"] == "slide"]
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(events)
        mine = events[i:end]
        clips = [(e["t"] - events[i]["t"], e["seconds"]) for e in mine if e["type"] == "sentence"]
        turns.append({
            "slide": events[i]["place"]["index"],
            **metrics.playback(clips),
            "sentences": len(clips),
            "diagrams": sum(1 for e in mine if e["type"] == "diagram"),
            "warnings": sum(1 for e in mine if e["type"] in ("warn", "unspoken")),
            "stuck": sum(1 for e in mine if e["type"] == "state" and e["state"] == "stuck"),
            "seq": [["SAY", e["text"]] if e["type"] == "sentence" else
                    ["DRAW", e["code"]] if e["type"] == "diagram" else
                    ["UNSPOKEN", e["text"]] if e["type"] == "unspoken" else ["WARN", e["message"]]
                    for e in mine if e["type"] in ("sentence", "diagram", "unspoken", "warn")],
        })
    return turns


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8770/ws")
    ap.add_argument("--host", required=True, help="which host is behind the server, for the log")
    ap.add_argument("--model", default=None)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--attend", action="store_true", help="sit through a lecture on Professor's own server")
    args = ap.parse_args()

    run = log.run_id()
    if args.attend:
        record = await attend(args.url)
        log.save_transcript(run, f"{args.host}-server", record)
        for t in slide_turns(record["events"]):
            log.record({"kind": "latency", "host": args.host, "path": "server", "model": args.model,
                        "turn": "slide" if t["slide"] == 0 else "slide-later", "run": run, "n": 0,
                        "metrics": {k: v for k, v in t.items() if k != "seq"}})
            print(f"slide {t['slide'] + 1}  first audio {t['first_audio_s']}s  dead air {t['dead_air_s']}s  "
                  f"{t['sentences']} sentences  {t['diagrams']} diagrams  {t['warnings']} warnings", flush=True)
        unexplained = metrics.unexplained(record["events"])
        print(f"{len(unexplained)} stretches where what was heard and the state shown disagreed"
              + "".join(f"\n  {u}" for u in unexplained))
        return
    for n in range(args.runs):
        turns = await session(args.url)
        log.save_transcript(run, f"{args.host}-prototype-{n}", [t for _, t in turns])
        for name, t in turns:
            m = {k: v for k, v in t.items() if k not in ("cmd", "seq")}
            log.record({"kind": "latency", "host": args.host, "path": "prototype", "model": args.model,
                        "turn": name, "run": run, "n": n, "metrics": m})
            print(f"run {n} {name:12} first audio {t['first_audio_s']}s  total {t['total_s']}s  "
                  f"dead air {t['dead_air_s']}s  {t['sentences']} sentences  {t['diagrams']} diagrams  "
                  f"{t['warnings']} warnings  {t['errors']} errors", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
