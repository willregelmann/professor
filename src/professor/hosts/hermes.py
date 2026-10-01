"""Hermes Agent as the lecturer, through its gateway's streaming chat completions.

Lifted from the prototype. The gateway keeps the conversation itself: it hands
back a session id with the first reply, and sending that id with later turns
continues the same conversation.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp


def key_from_env_file(path: Path | str) -> str:
    """The gateway's key, read from its own .env at run time. It is never logged
    and never sent to a listener."""
    for line in Path(path).expanduser().read_text().splitlines():
        if line.startswith("API_SERVER_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ValueError(f"API_SERVER_KEY not found in {path}")


def find_key(env_file: Path | str | None = None) -> str:
    """HERMES_API_KEY if it's set, otherwise the gateway's .env on this machine."""
    if key := os.environ.get("HERMES_API_KEY"):
        return key
    return key_from_env_file(env_file or Path.home() / ".hermes" / ".env")


class HermesHost:
    def __init__(self, url: str, key: str, *, idle_timeout_s: float = 120):
        self.url = url.rstrip("/") + "/v1/chat/completions"
        self._key = key
        self._idle_timeout_s = idle_timeout_s
        self.session = ""
        self._http: aiohttp.ClientSession | None = None
        self._response: aiohttp.ClientResponse | None = None

    async def turn(self, prompt: str) -> AsyncIterator[str]:
        if self._http is None:
            self._http = aiohttp.ClientSession()
        headers = {"Authorization": f"Bearer {self._key}"}
        if self.session:
            headers["X-Hermes-Session-Id"] = self.session
        body = {"model": "hermes", "stream": True, "messages": [{"role": "user", "content": prompt}]}
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=self._idle_timeout_s)
        async with self._http.post(self.url, json=body, headers=headers, timeout=timeout) as response:
            self._response = response
            if response.status != 200:
                # The body isn't passed on: it's the gateway's own, and listeners see this message.
                raise RuntimeError(f"the Hermes gateway answered {response.status}")
            self.session = response.headers.get("X-Hermes-Session-Id", self.session)
            async for raw in response.content:
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

    async def cancel(self) -> None:
        """Hang up on the turn in progress."""
        if self._response is not None:
            self._response.close()
            self._response = None

    async def close(self) -> None:
        await self.cancel()
        if self._http is not None:
            await self._http.close()
            self._http = None
