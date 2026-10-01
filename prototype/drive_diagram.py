#!/usr/bin/env python3
"""Headless driver for the diagram path: records the ORDER of speech vs diagram frames."""
import asyncio, json, time
import aiohttp

async def main():
    out = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:" + __import__("os").environ.get("PRESENTER_PORT","8770") + "/ws", max_msg_size=0) as ws:
            deck = json.loads((await ws.receive()).data)
            out.append({"deck_diagrams": [bool(x["diagram"]) for x in deck["slides"]]})
            async def turn(cmd):
                t0 = time.monotonic(); seq = []; first = None
                await ws.send_json(cmd)
                async for m in ws:
                    if m.type == aiohttp.WSMsgType.BINARY:
                        if first is None: first = round(time.monotonic() - t0, 2)
                        continue
                    d = json.loads(m.data)
                    if d["type"] == "sentence": seq.append(("SAY", d["text"]))
                    elif d["type"] == "diagram": seq.append(("DRAW", d["code"]))
                    elif d["type"] in ("warn", "error"): seq.append((d["type"].upper(), d.get("message") or d.get("error")))
                    elif d["type"] == "done":
                        return {"cmd": cmd, "first_audio": first, "stats": {k: d.get(k) for k in ("first_audio_s","total_s","sentences","diagrams")}, "seq": seq}
            out.append(await turn({"cmd": "question", "who": "Marcus",
                "text": "How does a message actually get from you to Ash and back? Walk me through the hops."}))
    print(json.dumps(out, indent=1))

asyncio.run(main())
