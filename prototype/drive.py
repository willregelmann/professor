#!/usr/bin/env python3
"""Headless driver: plays the role of the browser, records what actually came back."""
import asyncio, json, sys, time
import aiohttp

async def main():
    out = {"turns": []}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8770/ws", max_msg_size=0) as ws:
            async def turn(cmd):
                t0 = time.monotonic(); first_bin = None; nbytes = 0; sentences = []
                await ws.send_json(cmd)
                async for m in ws:
                    if m.type == aiohttp.WSMsgType.BINARY:
                        nbytes += len(m.data)
                        if first_bin is None: first_bin = time.monotonic() - t0
                    else:
                        d = json.loads(m.data)
                        if d["type"] == "sentence": sentences.append(d["text"])
                        if d["type"] == "error": return {"cmd": cmd, "error": d["message"]}
                        if d["type"] == "done":
                            return {"cmd": cmd, "first_audio_client_s": round(first_bin or -1, 2),
                                    "server": {k: d[k] for k in ("first_audio_s", "total_s", "sentences")},
                                    "audio_bytes": nbytes, "text": " ".join(sentences)}
            deck = json.loads((await ws.receive()).data)
            out["deck"] = deck["slides"]
            out["turns"].append(await turn({"cmd": "slide", "n": 0}))
            out["turns"].append(await turn({"cmd": "question", "who": "Priya", "text": "Is this narration actually live, or did you record it earlier?"}))
            out["turns"].append(await turn({"cmd": "slide", "n": 1}))
    print(json.dumps(out, indent=1))

asyncio.run(main())
