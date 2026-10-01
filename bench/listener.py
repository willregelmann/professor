"""A headless listener: plays the page's role over the websocket and records what
a listener would have experienced.

It speaks the prototype's protocol (prototype/index.html), so it measures the
prototype today and the real server once that keeps the same protocol.

    python -m bench.listener --url http://ha-pi.local:8770/ws --host hermes --runs 5

Each run is a fresh connection, which is a fresh host session: a slide turn that
carries the standing instructions, a question, then a second slide.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import aiohttp

from bench import log, metrics

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


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8770/ws")
    ap.add_argument("--host", required=True, help="which host is behind the server, for the log")
    ap.add_argument("--model", default=None)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    run = log.run_id()
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
