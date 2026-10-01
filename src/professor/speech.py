"""The speech engine, behind one function: text in, audio out.

edge-tts, as in the prototype. A sentence that can't be spoken comes back as
empty audio, whichever way the engine failed (older versions return nothing,
newer ones raise). Callers must treat empty audio as a failure and never send it
on: an empty clip is inert in a browser and deadlocks the player.
"""
from __future__ import annotations

import os

import edge_tts
from edge_tts.exceptions import NoAudioReceived

VOICE = os.environ.get("PROFESSOR_VOICE", "en-US-AriaNeural")
MP3_BITS_PER_S = 48_000  # edge-tts's default output is 48 kbit/s mono mp3


async def speak(text: str, voice: str = VOICE) -> bytes:
    out = bytearray()
    try:
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                out += chunk["data"]
    except NoAudioReceived:
        return b""
    return bytes(out)


def duration_s(audio: bytes) -> float:
    """How long a clip plays for, from its size. Close enough for measuring dead air."""
    return len(audio) * 8 / MP3_BITS_PER_S
