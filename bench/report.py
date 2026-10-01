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
        groups[(host, line["path"], line["turn"])].append(line["metrics"])

    print(f"{'host':36} {'turn':12} {'n':>2}  " + "  ".join(f"{c:>22}" for c in COLUMNS) + "  warnings")
    for (host, path, turn), ms in groups.items():
        cells = []
        for c in COLUMNS:
            s = metrics.summarize([m.get(c) for m in ms])
            cells.append(f"{s['median']:6.2f} ({s['min']:.2f}-{s['max']:.2f})".rjust(22) if s["n"] else " " * 22)
        warnings = sum(m.get("warnings", 0) + m.get("errors", 0) for m in ms)
        print(f"{host + ' / ' + path:36} {turn:12} {len(ms):>2}  " + "  ".join(cells) + f"  {warnings:>8}")


if __name__ == "__main__":
    main()
