"""The server: serves the page and holds a websocket to each listener.

It decides nothing. Everything a listener sees and hears comes from the lecture,
and everything a listener does is passed to it.
"""
from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web

from professor.lecture import Lecture

PAGE = Path(__file__).resolve().parent / "page"
LECTURE = web.AppKey("lecture", Lecture)
HEARTBEAT_S = web.AppKey("heartbeat_s", float)


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(PAGE / "index.html")


async def listen(request: web.Request) -> web.WebSocketResponse:
    lecture = request.app[LECTURE]
    # No compression. With it on, aiohttp 3.14 rejects the first frame a browser
    # sends after a heartbeat ("non-zero reserved bits") and drops the listener.
    # The audio is already compressed, so little is lost.
    ws = web.WebSocketResponse(heartbeat=request.app[HEARTBEAT_S], compress=False)
    await ws.prepare(request)

    async def to_page(msg: dict) -> None:
        await ws.send_json(msg)

    # Someone who arrives late sees the lecture as it is now. Taking the snapshot
    # and subscribing happen together, so nothing falls between them.
    snapshot = lecture.snapshot()
    lecture.subscribe(to_page)
    await to_page(snapshot)
    try:
        async for raw in ws:
            if raw.type != web.WSMsgType.TEXT:
                continue
            try:
                msg = json.loads(raw.data)
                cmd = msg.get("cmd")
                if cmd == "start":
                    lecture.start()
                elif cmd == "pause":
                    await lecture.pause()
                elif cmd == "resume":
                    await lecture.resume()
                elif cmd == "retry":
                    lecture.retry()
                elif cmd == "end":
                    await lecture.end()
                elif cmd == "draw_failed":
                    await lecture.draw_failed(str(msg["id"]), str(msg.get("error", "")))
                elif cmd == "slide_failed":
                    await lecture.slide_failed(int(msg["revision"]), str(msg.get("error", "")))
                else:
                    await to_page({"type": "warn", "message": f"The server didn't understand {cmd!r}."})
            except (ValueError, KeyError, TypeError) as exc:
                await to_page({"type": "warn", "message": f"The server couldn't read that: {exc}"})
    finally:
        lecture.unsubscribe(to_page)
    return ws


def make_app(lecture: Lecture, *, heartbeat_s: float = 30) -> web.Application:
    app = web.Application()
    app[LECTURE] = lecture
    app[HEARTBEAT_S] = heartbeat_s
    app.router.add_get("/", index)
    app.router.add_get("/ws", listen)
    app.router.add_static("/static/", PAGE / "static")

    async def close_lecture(app: web.Application) -> None:
        await app[LECTURE].close()
    app.on_shutdown.append(close_lecture)
    return app
