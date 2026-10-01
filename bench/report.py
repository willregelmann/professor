"""Summarize the latency lines in the results log, one row per host and turn.

    python -m bench.report
"""
from __future__ import annotations

import json
from collections import defaultdict

from bench import log, metrics

COLUMNS = ("first_audio_s", "total_s", "audio_s", "dead_air_s")


def main() -> None:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for raw in log.DEFAULT_LOG.read_text().splitlines():
        line = json.loads(raw)
        if line.get("kind") != "latency":
            continue
        passage = (line.get("config") or {}).get("passage")
        host = line["host"] + (f" ({passage})" if passage else "")
        path = line["path"] + (f" ({line['deck']})" if line.get("deck") else "")
        groups[(host, path, line["turn"])].append(line["metrics"])

    print(f"{'host':36} {'turn':12} {'n':>2}  " + "  ".join(f"{c:>22}" for c in COLUMNS) + "  warnings")
    lectures = [json.loads(raw) for raw in log.DEFAULT_LOG.read_text().splitlines() if '"kind": "lecture"' in raw]
    for (host, path, turn), ms in groups.items():
        cells = []
        for c in COLUMNS:
            # On Professor's own server a slide's clips are spread across questions and
            # stops, so gaps between them aren't dead air. bench.metrics.unexplained is
            # the check there.
            s = metrics.summarize([] if path.startswith("server") and c == "dead_air_s"
                                  else [m.get(c) for m in ms])
            cells.append(f"{s['median']:6.2f} ({s['min']:.2f}-{s['max']:.2f})".rjust(22) if s["n"] else " " * 22)
        warnings = sum(m.get("warnings", 0) + m.get("errors", 0) for m in ms)
        print(f"{host + ' / ' + path:36} {turn:12} {len(ms):>2}  " + "  ".join(cells) + f"  {warnings:>8}")

    for host, deck in sorted({(line["host"], line.get("deck", "prototype")) for line in lectures}):
        ms = [line["metrics"] for line in lectures
              if (line["host"], line.get("deck", "prototype")) == (host, deck)]
        qs = [q for m in ms for q in m["questions"]]
        ends = {final: sum(1 for q in qs if q["final"] == final) for final in sorted({q["final"] for q in qs})}
        states = sorted({" > ".join(q["states"]) for q in qs})
        print(f"\n{host} ({deck} deck): {len(ms)} lectures, {len(qs)} questions, ended {ends}; "
              f"went through {states}; "
              f"{sum(1 for q in qs if 'put_off' in q['states'])} put off and come back to; "
              f"every question settled in {sum(m['all_settled'] for m in ms)}/{len(ms)}; "
              f"heard within {max(q['heard_after_s'] for q in qs):.3f}s; "
              f"first word of a reply {metrics.summarize([q['taken_to_first_audio_s'] for q in qs])}; "
              f"stop to silence {metrics.summarize([m['stop_to_silence_s'] for m in ms])}; "
              f"{sum(len(m['unexplained']) for m in ms)} unexplained silences")


if __name__ == "__main__":
    main()
