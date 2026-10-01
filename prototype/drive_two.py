#!/usr/bin/env python3
"""Two questions in ONE session: turn 1 carries the preamble, turn 2 does not. Records first audio per turn."""
import asyncio, json, os, time
import aiohttp
QS = ["How does a message actually get from you to Ash and back? Walk me through the hops.",
      "What happens if Ash's gateway is down when you send?"]
async def main():
    out = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:" + os.environ.get("PRESENTER_PORT", "8771") + "/ws", max_msg_size=0) as ws:
            await ws.receive()
            for q in QS:
                t0 = time.monotonic(); first = None; first_text = None
                await ws.send_json({"cmd": "question", "who": "Marcus", "text": q})
                async for m in ws:
                    if m.type == aiohttp.WSMsgType.BINARY:
                        if first is None: first = round(time.monotonic() - t0, 2)
                        continue
                    d = json.loads(m.data)
                    if d["type"] == "sentence" and first_text is None: first_text = round(time.monotonic() - t0, 2)
                    if d["type"] in ("warn", "error"): out.append(d)
                    if d["type"] == "done":
                        out.append({"q": q[:40], "client_first_audio": first, "first_sentence_frame": first_text,
                                    "stats": {k: d.get(k) for k in ("first_audio_s", "total_s", "sentences", "diagrams")}})
                        break
    print(json.dumps(out, indent=1))
asyncio.run(main())
