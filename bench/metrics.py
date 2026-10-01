"""What a listener would have experienced, worked out from when each clip arrived
and how long it plays for."""
from __future__ import annotations

import statistics

from professor.speech import MP3_BITS_PER_S


def playback(clips: list[tuple[float, float]]) -> dict:
    """Play clips back to back, each starting when it has arrived and the one
    before it has finished. `clips` is (arrival_s, duration_s) in arrival order.

    dead_air_s is silence after the first word: time the listener spent waiting
    for a clip that hadn't arrived yet.
    """
    if not clips:
        return {"first_audio_s": None, "audio_s": 0.0, "dead_air_s": 0.0, "longest_gap_s": 0.0, "stalls": 0}
    gaps = []
    end = clips[0][0] + clips[0][1]
    for arrival, duration in clips[1:]:
        if arrival > end:
            gaps.append(arrival - end)
        end = max(arrival, end) + duration
    return {
        "first_audio_s": round(clips[0][0], 2),
        "audio_s": round(sum(d for _, d in clips), 2),
        "dead_air_s": round(sum(gaps), 2),
        "longest_gap_s": round(max(gaps, default=0.0), 2),
        "stalls": sum(1 for g in gaps if g > 0.5),  # gaps long enough to notice
    }


def unexplained(events: list[dict], tolerance_s: float = 0.3) -> list[str]:
    """Stretches where what a listener heard and the state they were shown disagreed:
    silence while the page said "speaking", or speech while it said anything else.

    `events` is what bench.listener.attend recorded. Clips play back to back, each
    starting when it has arrived and the one before it has finished. A lecture
    that was paused can't be checked this way, because pausing stops a clip midway.
    """
    marks: list[tuple[float, str, object]] = []  # (time, what changed, value)
    end = 0.0
    for e in events:
        if e["type"] == "sentence":
            begin = max(e["t"], end)
            end = begin + e["seconds"]
            marks += [(begin, "audio", True), (end, "audio", False)]
        elif e["type"] == "state":
            marks.append((e["t"], "state", e["state"]))
    if any(value == "paused" for _, kind, value in marks if kind == "state"):
        raise ValueError("a paused lecture can't be checked for unexplained silence")
    marks.sort(key=lambda m: m[0])

    out: list[str] = []
    playing, state, since = False, None, None

    def disagree() -> bool:
        return state is not None and playing != (state == "speaking")

    for t, kind, value in [*marks, (marks[-1][0] if marks else 0.0, "end", None)]:
        was, heard, shown = disagree(), playing, state
        if kind == "audio":
            playing = value
        elif kind == "state":
            state = value
        now = disagree() and kind != "end"
        if was and not now:
            if t - since > tolerance_s:
                out.append(f"{since:.2f}s to {t:.2f}s: {'speech' if heard else 'silence'} while the state "
                           f"was \"{shown}\"")
            since = None
        elif now and not was:
            since = t
    return out


def mp3_duration_s(nbytes: int) -> float:
    return nbytes * 8 / MP3_BITS_PER_S


def summarize(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": round(statistics.median(values), 2),
            "min": round(min(values), 2), "max": round(max(values), 2)}
